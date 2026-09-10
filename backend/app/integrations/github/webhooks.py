"""Webhook signature verification.

A webhook is an unauthenticated HTTP request from the open internet that asks
DevPilot to do work and post comments on someone's pull request. The signature
is the only thing separating GitHub from anyone who guessed the URL.

Two details matter more than the rest, and both are easy to get wrong:

1. **Verify the raw bytes.** The HMAC covers exactly what GitHub sent. Parsing
   the JSON and re-serialising it produces different bytes -- key order,
   whitespace and unicode escaping all shift -- so a check against re-serialised
   JSON either fails constantly or has been loosened until it proves nothing.

2. **Compare in constant time.** A naive `==` on the digest returns as soon as
   two bytes differ, so the time it takes leaks how much of the prefix was
   correct. That is enough to recover a valid signature byte by byte.
"""

from __future__ import annotations

import hashlib
import hmac

from app.core.logging import get_logger

logger = get_logger(__name__)

# GitHub sends the SHA-256 HMAC here, prefixed with the algorithm name.
SIGNATURE_HEADER = "X-Hub-Signature-256"
SIGNATURE_PREFIX = "sha256="

# The older SHA-1 header GitHub still sends. Deliberately unused: SHA-1 is
# considered broken for signature purposes, and accepting either would let an
# attacker choose the weaker one.
LEGACY_SIGNATURE_HEADER = "X-Hub-Signature"

DELIVERY_HEADER = "X-GitHub-Delivery"
EVENT_HEADER = "X-GitHub-Event"


class InvalidSignatureError(Exception):
    """The request did not carry a valid signature from GitHub."""


def compute_signature(payload: bytes, secret: str) -> str:
    """Return the `sha256=...` signature GitHub would send for `payload`."""
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify_signature(payload: bytes, signature_header: str | None, secret: str) -> None:
    """Raise `InvalidSignatureError` unless `signature_header` matches `payload`.

    Returns `None` on success rather than a boolean, so a caller cannot forget
    to check the result -- a silently ignored `False` would accept every forged
    request.
    """
    if not secret:
        # Refusing everything is the only safe behaviour: with no secret there
        # is nothing to verify against, and accepting unverified webhooks would
        # let anyone who knows the URL create review jobs.
        raise InvalidSignatureError("No webhook secret is configured.")

    if not signature_header:
        raise InvalidSignatureError("Request is missing the signature header.")

    if not signature_header.startswith(SIGNATURE_PREFIX):
        # Refuse anything that is not explicitly SHA-256, including the legacy
        # SHA-1 header, rather than letting the sender pick the algorithm.
        raise InvalidSignatureError("Signature is not a SHA-256 signature.")

    expected = compute_signature(payload, secret)
    if not hmac.compare_digest(expected, signature_header):
        raise InvalidSignatureError("Signature does not match the request body.")
