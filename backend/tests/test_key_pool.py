"""Tests for API key rotation.

The pool exists to make a free tier usable, so the behaviour worth pinning is
what happens when keys run out: it must skip a throttled key, retire a rejected
one, and report how long to wait when nothing is usable.
"""

from __future__ import annotations

import pytest

from app.integrations.llm.key_pool import (
    ApiKeyPool,
    NoKeysAvailableError,
)


class TestConstruction:
    def test_refuses_an_empty_pool(self) -> None:
        """A pool with no keys would fail on first use with a confusing error."""
        with pytest.raises(ValueError, match="at least one key"):
            ApiKeyPool([])

    def test_ignores_blank_entries(self) -> None:
        """A trailing comma in the .env file is the common way to get one."""
        pool = ApiKeyPool(["a", "  ", "", "b"])

        assert len(pool) == 2

    def test_strips_surrounding_whitespace(self) -> None:
        pool = ApiKeyPool([" a ", "b"])

        assert pool.acquire().secret == "a"

    def test_deduplicates(self) -> None:
        """The same key twice would take double the traffic and hit its limit
        first, which looks exactly like rotation not working."""
        pool = ApiKeyPool(["a", "b", "a"])

        assert len(pool) == 2


class TestRotation:
    def test_rotates_on_every_request(self) -> None:
        """Not just on failure: free-tier limits are per minute, so load must be
        spread rather than concentrated until a key complains."""
        pool = ApiKeyPool(["a", "b", "c"])

        assert [pool.acquire().secret for _ in range(6)] == ["a", "b", "c", "a", "b", "c"]

    def test_a_single_key_pool_keeps_returning_it(self) -> None:
        pool = ApiKeyPool(["only"])

        assert [pool.acquire().secret for _ in range(3)] == ["only"] * 3

    def test_labels_never_contain_the_secret(self) -> None:
        """Labels reach the logs; keys must not."""
        pool = ApiKeyPool(["super-secret-value"])

        leased = pool.acquire()

        assert "super-secret" not in leased.label
        assert leased.label == "key-1"


class TestCoolDown:
    def test_skips_a_cooling_key(self) -> None:
        pool = ApiKeyPool(["a", "b"])
        first = pool.acquire()

        pool.cool_down(first, 60)

        assert [pool.acquire().secret for _ in range(3)] == ["b", "b", "b"]

    def test_raises_when_every_key_is_cooling(self) -> None:
        pool = ApiKeyPool(["a", "b"])
        pool.cool_down(pool.acquire(), 30)
        pool.cool_down(pool.acquire(), 90)

        with pytest.raises(NoKeysAvailableError) as caught:
            pool.acquire()

        # The *shortest* wait: waiting 90s when a key frees up in 30 wastes a
        # minute of a quota that is already scarce.
        assert caught.value.retry_after_seconds == 30

    def test_a_cooled_key_returns_when_the_wait_elapses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = {"now": 1_000.0}
        monkeypatch.setattr("app.integrations.llm.key_pool.time.monotonic", lambda: clock["now"])
        pool = ApiKeyPool(["a"])
        pool.cool_down(pool.acquire(), 30)

        with pytest.raises(NoKeysAvailableError):
            pool.acquire()

        clock["now"] += 31

        assert pool.acquire().secret == "a"

    def test_a_zero_wait_still_parks_the_key_briefly(self) -> None:
        """Rounding a sub-second retry_after to 0 would spin on the same key."""
        pool = ApiKeyPool(["a", "b"])
        pool.cool_down(pool.acquire(), 0)

        assert pool.acquire().secret == "b"


class TestDisable:
    def test_a_disabled_key_is_never_returned(self) -> None:
        """A revoked key will not start working; retrying it forever converts a
        config mistake into a permanent one-in-N failure rate."""
        pool = ApiKeyPool(["a", "b"])

        pool.disable(pool.acquire(), reason="HTTP 401")

        assert [pool.acquire().secret for _ in range(3)] == ["b", "b", "b"]

    def test_raises_when_every_key_is_disabled(self) -> None:
        pool = ApiKeyPool(["a", "b"])
        pool.disable(pool.acquire(), reason="HTTP 401")
        pool.disable(pool.acquire(), reason="HTTP 401")

        with pytest.raises(NoKeysAvailableError, match="rejected"):
            pool.acquire()

    def test_disabling_does_not_resurrect_on_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = {"now": 1_000.0}
        monkeypatch.setattr("app.integrations.llm.key_pool.time.monotonic", lambda: clock["now"])
        pool = ApiKeyPool(["a"])
        pool.disable(pool.acquire(), reason="HTTP 403")

        clock["now"] += 10_000

        with pytest.raises(NoKeysAvailableError):
            pool.acquire()


class TestUsableCount:
    def test_reports_how_many_keys_are_currently_usable(self) -> None:
        pool = ApiKeyPool(["a", "b", "c"])
        assert pool.usable == 3

        pool.cool_down(pool.acquire(), 60)
        assert pool.usable == 2

        pool.disable(pool.acquire(), reason="HTTP 401")
        assert pool.usable == 1
