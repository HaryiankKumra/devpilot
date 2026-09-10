"""Tests for webhook signature verification.

This is the only thing separating GitHub from anyone who guessed the webhook
URL, so the negative cases matter more than the positive one.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from app.integrations.github.webhooks import (
    InvalidSignatureError,
    compute_signature,
    verify_signature,
)

SECRET = "a-webhook-secret"
BODY = b'{"action":"opened","number":1}'


def valid_signature(body: bytes = BODY, secret: str = SECRET) -> str:
    return compute_signature(body, secret)


class TestValidSignatures:
    def test_accepts_a_correct_signature(self) -> None:
        verify_signature(BODY, valid_signature(), SECRET)

    def test_matches_githubs_own_construction(self) -> None:
        """Computed independently here, so a bug in `compute_signature` cannot
        make both sides agree on the wrong answer."""
        expected = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()

        assert valid_signature() == f"sha256={expected}"

    def test_accepts_an_empty_body(self) -> None:
        """A signed empty body is still a valid signature over zero bytes."""
        verify_signature(b"", compute_signature(b"", SECRET), SECRET)


class TestRejectedSignatures:
    def test_rejects_a_missing_header(self) -> None:
        with pytest.raises(InvalidSignatureError, match="missing"):
            verify_signature(BODY, None, SECRET)

    def test_rejects_an_empty_header(self) -> None:
        with pytest.raises(InvalidSignatureError):
            verify_signature(BODY, "", SECRET)

    def test_rejects_a_signature_for_different_bytes(self) -> None:
        """The core guarantee: the signature must cover this exact body."""
        other = compute_signature(b'{"action":"closed"}', SECRET)

        with pytest.raises(InvalidSignatureError, match="does not match"):
            verify_signature(BODY, other, SECRET)

    def test_rejects_a_signature_made_with_another_secret(self) -> None:
        forged = compute_signature(BODY, "the-attackers-secret")

        with pytest.raises(InvalidSignatureError):
            verify_signature(BODY, forged, SECRET)

    def test_rejects_a_body_altered_after_signing(self) -> None:
        signature = valid_signature()
        tampered = BODY.replace(b"opened", b"closed")

        with pytest.raises(InvalidSignatureError):
            verify_signature(tampered, signature, SECRET)

    def test_rejects_a_single_flipped_character(self) -> None:
        signature = valid_signature()
        flipped = signature[:-1] + ("0" if signature[-1] != "0" else "1")

        with pytest.raises(InvalidSignatureError):
            verify_signature(BODY, flipped, SECRET)

    def test_rejects_the_legacy_sha1_scheme(self) -> None:
        """SHA-1 is broken for signatures; accepting either would let the sender
        choose the weaker algorithm."""
        sha1 = hmac.new(SECRET.encode(), BODY, hashlib.sha1).hexdigest()

        with pytest.raises(InvalidSignatureError, match="not a SHA-256"):
            verify_signature(BODY, f"sha1={sha1}", SECRET)

    def test_rejects_a_bare_digest_without_the_prefix(self) -> None:
        digest = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()

        with pytest.raises(InvalidSignatureError):
            verify_signature(BODY, digest, SECRET)

    def test_rejects_everything_when_no_secret_is_configured(self) -> None:
        """With no secret there is nothing to verify against, so accepting would
        let anyone who knows the URL create review jobs."""
        with pytest.raises(InvalidSignatureError, match="No webhook secret"):
            verify_signature(BODY, valid_signature(), "")

    def test_a_signature_is_not_transferable_between_secrets(self) -> None:
        with pytest.raises(InvalidSignatureError):
            verify_signature(BODY, valid_signature(secret="secret-a"), "secret-b")


class TestConstantTimeComparison:
    def test_uses_hmac_compare_digest(self) -> None:
        """A plain `==` returns early on the first differing byte, leaking how
        much of the prefix was right -- enough to recover a valid signature.

        Timing cannot be asserted reliably in a unit test, so this pins the
        implementation instead.
        """
        import inspect

        from app.integrations.github import webhooks

        source = inspect.getsource(webhooks.verify_signature)
        assert "compare_digest" in source
