"""Rotation across several API keys.

Free tiers are metered per key, so holding more than one key and spreading
requests across them is the difference between a review pipeline that runs and
one that spends most of its day rate-limited. This pool is what makes that
tidy instead of a pile of `try: key1 except: key2`.

Three behaviours, each earning its place:

* **Round-robin on every request**, not only on failure. Free-tier limits are
  usually requests *per minute*, so the goal is to spread load evenly rather
  than to hammer one key until it complains. Failover-only rotation would keep
  key one permanently at its limit while keys two through five sat idle.
* **Cool-down on 429.** A key that has just been rate-limited is skipped until
  the provider's own `retry_after` has elapsed. Without this, rotation walks
  straight back onto the exhausted key a few requests later.
* **Disable on an authentication failure.** A revoked or mistyped key will never
  start working, so retrying it every N requests forever just converts a
  configuration mistake into a permanent one-in-N failure rate.

**Keys are never logged.** Each is identified by its position in the pool, so a
log line can say which key misbehaved without putting the secret in a log
aggregator.

**Scope: this pool lives in one process.** Celery's prefork workers are separate
processes, so four workers hold four independent pools and none of them can see
the others' cool-downs. The failure mode is benign -- a second process tries a
key the first has parked, gets a 429, and parks it too -- but it does mean
rotation spreads load approximately rather than exactly. Moving the cool-down
into Redis, which this project already runs for rate limiting, is the fix if
quota ever gets tight enough to need it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger(__name__)

# Used when a provider rate-limits without saying for how long.
DEFAULT_COOL_DOWN_SECONDS = 60


class NoKeysAvailableError(Exception):
    """Every key is either cooling down or disabled.

    Carries the shortest remaining cool-down so the caller can back off for
    exactly that long rather than guessing.
    """

    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass
class _KeyState:
    """One key and what we currently believe about it."""

    secret: str
    label: str
    # Monotonic timestamp before which this key must not be used again.
    available_at: float = 0.0
    disabled: bool = False
    uses: int = field(default=0)


@dataclass(frozen=True)
class LeasedKey:
    """A key handed out for one request.

    The pool is told the outcome through this handle rather than through the
    key itself, so the secret never has to be passed back around.
    """

    secret: str
    label: str


class ApiKeyPool:
    """Hands out API keys in rotation, skipping ones that are unusable."""

    def __init__(self, keys: Sequence[str]) -> None:
        cleaned = [key.strip() for key in keys if key and key.strip()]
        if not cleaned:
            raise ValueError("An API key pool needs at least one key.")

        # Deduplicate while preserving order. The same key pasted twice would
        # otherwise get double the share of traffic and hit its limit first,
        # which looks exactly like the rotation not working.
        seen: set[str] = set()
        unique: list[str] = []
        for key in cleaned:
            if key not in seen:
                seen.add(key)
                unique.append(key)

        if len(unique) != len(cleaned):
            logger.warning(
                "llm.key_pool.duplicates_ignored",
                supplied=len(cleaned),
                unique=len(unique),
            )

        self._states = [
            _KeyState(secret=key, label=f"key-{index}") for index, key in enumerate(unique, start=1)
        ]
        self._next = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._states)

    @property
    def usable(self) -> int:
        """How many keys are neither disabled nor cooling down right now."""
        now = time.monotonic()
        with self._lock:
            return sum(
                1 for state in self._states if not state.disabled and state.available_at <= now
            )

    def acquire(self) -> LeasedKey:
        """Return the next usable key, or raise `NoKeysAvailableError`."""
        now = time.monotonic()

        with self._lock:
            total = len(self._states)
            for offset in range(total):
                index = (self._next + offset) % total
                state = self._states[index]
                if state.disabled or state.available_at > now:
                    continue

                # Advance past the key we are handing out, so the next caller
                # starts at the following one.
                self._next = (index + 1) % total
                state.uses += 1
                return LeasedKey(secret=state.secret, label=state.label)

            raise NoKeysAvailableError(
                self._exhaustion_message(now),
                retry_after_seconds=self._shortest_wait(now),
            )

    def cool_down(self, leased: LeasedKey, seconds: int) -> None:
        """Park a rate-limited key until the provider says it is usable again."""
        wait = max(1, seconds)
        with self._lock:
            state = self._find(leased.label)
            if state is not None:
                state.available_at = time.monotonic() + wait

        logger.info("llm.key_pool.cooled_down", key=leased.label, seconds=wait)

    def disable(self, leased: LeasedKey, reason: str) -> None:
        """Retire a key permanently: it was rejected, not merely throttled."""
        with self._lock:
            state = self._find(leased.label)
            if state is not None:
                state.disabled = True
            remaining = sum(1 for candidate in self._states if not candidate.disabled)

        # Error, not warning: a pool quietly shrinking to one key is how a
        # "sometimes it is slow" report starts.
        logger.error(
            "llm.key_pool.key_disabled",
            key=leased.label,
            reason=reason,
            keys_remaining=remaining,
        )

    # --- internals -----------------------------------------------------------

    def _find(self, label: str) -> _KeyState | None:
        return next((state for state in self._states if state.label == label), None)

    def _shortest_wait(self, now: float) -> int:
        """Seconds until the first key becomes usable again."""
        waits = [
            state.available_at - now
            for state in self._states
            if not state.disabled and state.available_at > now
        ]
        if not waits:
            # Everything is disabled rather than cooling: nothing will free up
            # on its own, but the caller still needs a number.
            return DEFAULT_COOL_DOWN_SECONDS
        return max(1, round(min(waits)))

    def _exhaustion_message(self, now: float) -> str:
        disabled = sum(1 for state in self._states if state.disabled)
        cooling = sum(
            1 for state in self._states if not state.disabled and state.available_at > now
        )
        if disabled and not cooling:
            return f"All {disabled} API keys were rejected by the provider."
        return f"All API keys are rate-limited ({cooling} cooling down, {disabled} disabled)."
