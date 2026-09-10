"""End-to-end tests for the webhook endpoint.

Covers the three things that make webhook handling correct rather than merely
working: signature enforcement, idempotency under redelivery, and the decision
of when a review is actually warranted.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Environment, Settings
from app.core.enums import PullRequestState, ReviewJobStatus
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.models.review_job import ReviewJob
from app.db.models.user import User
from app.db.models.webhook_event import WebhookEvent
from app.integrations.github.webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    compute_signature,
)
from app.main import create_app
from app.services import repositories as repository_service

WEBHOOK_URL = "/api/v1/webhooks/github"
WEBHOOK_SECRET = "test-webhook-secret"

GITHUB_REPO_ID = 900_000_001
INSTALLATION_ID = 10_000_001
SENDER_GITHUB_ID = 4_242_100


@pytest.fixture
def webhook_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"github_webhook_secret": SecretStr(WEBHOOK_SECRET)})


@pytest.fixture
def app(webhook_settings: Settings, db_session: Session) -> FastAPI:
    """Override the shared fixture so the app has a webhook secret configured."""
    from collections.abc import Iterator

    from app.api.deps import get_db

    application = create_app(webhook_settings)

    def override_get_db() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    return application


def post_webhook(
    client: TestClient,
    payload: dict[str, Any],
    *,
    event: str = "pull_request",
    delivery_id: str | None = None,
    secret: str = WEBHOOK_SECRET,
    sign: bool = True,
) -> Any:
    """Send a delivery the way GitHub would: raw JSON plus an HMAC over it."""
    body = json.dumps(payload).encode()
    headers = {
        EVENT_HEADER: event,
        DELIVERY_HEADER: delivery_id or str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    if sign:
        headers[SIGNATURE_HEADER] = compute_signature(body, secret)
    return client.post(WEBHOOK_URL, content=body, headers=headers)


def pull_request_payload(
    *,
    action: str = "opened",
    number: int = 42,
    head_sha: str = "a" * 40,
    draft: bool = False,
    state: str = "open",
    merged: bool = False,
    repo_id: int = GITHUB_REPO_ID,
) -> dict[str, Any]:
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "id": 800_000_001,
            "number": number,
            "title": "Add coupon validation",
            "state": state,
            "draft": draft,
            "merged": merged,
            "user": {"id": 4_242_009, "login": "demo-developer"},
            "head": {"ref": "feature/coupons", "sha": head_sha},
            "base": {"ref": "main", "sha": "b" * 40},
        },
        "repository": {
            "id": repo_id,
            "name": "checkout-service",
            "full_name": "devpilot-demo/checkout-service",
            "private": True,
            "default_branch": "main",
        },
        "installation": {"id": INSTALLATION_ID},
        "sender": {"id": SENDER_GITHUB_ID, "login": "demo-developer"},
    }


@pytest.fixture
def tracked_repository(db_session: Session, registered_user: User) -> Repository:
    """A repository DevPilot already knows about, owned by the test user."""
    from app.integrations.github.models import GitHubRepository

    repository, _ = repository_service.upsert_repository(
        db_session,
        owner=registered_user,
        remote=GitHubRepository(
            id=GITHUB_REPO_ID,
            name="checkout-service",
            full_name="devpilot-demo/checkout-service",
            private=True,
            default_branch="main",
        ),
        installation_id=INSTALLATION_ID,
    )
    db_session.commit()
    return repository


class TestSignatureEnforcement:
    def test_rejects_an_unsigned_delivery(self, client: TestClient) -> None:
        response = post_webhook(client, pull_request_payload(), sign=False)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_signature"

    def test_rejects_a_delivery_signed_with_the_wrong_secret(self, client: TestClient) -> None:
        response = post_webhook(client, pull_request_payload(), secret="not-the-secret")

        assert response.status_code == 401

    def test_rejects_a_body_altered_after_signing(self, client: TestClient) -> None:
        """Verification covers the raw bytes, so tampering invalidates it."""
        body = json.dumps(pull_request_payload()).encode()
        signature = compute_signature(body, WEBHOOK_SECRET)

        response = client.post(
            WEBHOOK_URL,
            content=body.replace(b"opened", b"closed"),
            headers={
                EVENT_HEADER: "pull_request",
                DELIVERY_HEADER: str(uuid.uuid4()),
                SIGNATURE_HEADER: signature,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 401

    def test_an_unsigned_delivery_writes_nothing(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Rejected requests must not reach the database at all."""
        post_webhook(client, pull_request_payload(), sign=False)

        assert db_session.execute(select(WebhookEvent)).first() is None


class TestIdempotency:
    def test_records_the_delivery_id(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        delivery_id = str(uuid.uuid4())

        post_webhook(client, pull_request_payload(), delivery_id=delivery_id)

        stored = db_session.execute(select(WebhookEvent)).scalar_one()
        assert stored.delivery_id == delivery_id

    def test_a_redelivery_is_acknowledged_without_repeating_the_work(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        """GitHub retries deliveries it believes failed. A retry must not create
        a second review."""
        delivery_id = str(uuid.uuid4())
        payload = pull_request_payload()

        first = post_webhook(client, payload, delivery_id=delivery_id)
        second = post_webhook(client, payload, delivery_id=delivery_id)

        assert first.status_code == 202
        assert first.json()["duplicate"] is False
        # 2xx, or GitHub keeps retrying forever.
        assert second.status_code == 200
        assert second.json()["duplicate"] is True

        jobs = db_session.execute(select(ReviewJob)).scalars().all()
        assert len(jobs) == 1

    def test_a_redelivery_does_not_duplicate_the_event_row(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        delivery_id = str(uuid.uuid4())

        for _ in range(3):
            post_webhook(client, pull_request_payload(), delivery_id=delivery_id)

        events = db_session.execute(select(WebhookEvent)).scalars().all()
        assert len(events) == 1

    def test_distinct_deliveries_of_the_same_event_are_both_recorded(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        """Idempotency keys on the delivery id, not the payload: GitHub can
        legitimately send the same event twice with different ids."""
        post_webhook(client, pull_request_payload(), delivery_id="delivery-one")
        post_webhook(client, pull_request_payload(), delivery_id="delivery-two")

        events = db_session.execute(select(WebhookEvent)).scalars().all()
        assert len(events) == 2

    def test_the_transaction_survives_a_duplicate(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        """The duplicate insert runs in a SAVEPOINT; without it the constraint
        violation would poison the transaction and the response would fail."""
        delivery_id = str(uuid.uuid4())
        post_webhook(client, pull_request_payload(), delivery_id=delivery_id)

        response = post_webhook(client, pull_request_payload(), delivery_id=delivery_id)

        assert response.status_code == 200
        # The session is still usable afterwards.
        assert db_session.execute(select(WebhookEvent)).scalars().all()


class TestMalformedRequests:
    def test_rejects_a_body_that_is_not_json(self, client: TestClient) -> None:
        body = b"this is not json"
        response = client.post(
            WEBHOOK_URL,
            content=body,
            headers={
                EVENT_HEADER: "pull_request",
                DELIVERY_HEADER: str(uuid.uuid4()),
                SIGNATURE_HEADER: compute_signature(body, WEBHOOK_SECRET),
            },
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "malformed_webhook"

    def test_rejects_a_json_body_that_is_not_an_object(self, client: TestClient) -> None:
        body = b"[1, 2, 3]"
        response = client.post(
            WEBHOOK_URL,
            content=body,
            headers={
                EVENT_HEADER: "pull_request",
                DELIVERY_HEADER: str(uuid.uuid4()),
                SIGNATURE_HEADER: compute_signature(body, WEBHOOK_SECRET),
            },
        )

        assert response.status_code == 400

    def test_rejects_a_signed_request_without_github_headers(self, client: TestClient) -> None:
        body = json.dumps(pull_request_payload()).encode()

        response = client.post(
            WEBHOOK_URL,
            content=body,
            headers={SIGNATURE_HEADER: compute_signature(body, WEBHOOK_SECRET)},
        )

        assert response.status_code == 400

    def test_records_a_payload_that_does_not_match_the_expected_shape(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        """Retrying cannot help -- GitHub would send the same bytes -- so it is
        recorded as failed rather than left for redelivery."""
        response = post_webhook(client, {"action": "opened"}, event="pull_request")

        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        stored = db_session.execute(select(WebhookEvent)).scalar_one()
        assert stored.status.value == "failed"
        assert stored.error_message


class TestEventDispatch:
    def test_answers_a_ping(self, client: TestClient) -> None:
        """GitHub sends this once when the webhook is created."""
        response = post_webhook(client, {"zen": "Keep it logically awesome."}, event="ping")

        assert response.status_code == 200
        assert response.json()["status"] == "processed"

    def test_ignores_an_event_devpilot_does_not_act_on(
        self, client: TestClient, db_session: Session
    ) -> None:
        response = post_webhook(client, {"action": "created"}, event="issue_comment")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        # Still recorded, so its retry is recognised as a duplicate.
        assert db_session.execute(select(WebhookEvent)).scalar_one()

    def test_ignores_a_pull_request_for_an_untracked_repository(
        self, client: TestClient, db_session: Session
    ) -> None:
        response = post_webhook(client, pull_request_payload(repo_id=123_456))

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert db_session.execute(select(ReviewJob)).first() is None


class TestPullRequestHandling:
    def test_records_the_pull_request(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        post_webhook(client, pull_request_payload())

        stored = db_session.execute(select(PullRequest)).scalar_one()
        assert stored.number == 42
        assert stored.author_login == "demo-developer"
        assert stored.state is PullRequestState.OPEN

    def test_queues_a_review(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        response = post_webhook(client, pull_request_payload())

        assert response.status_code == 202
        assert response.json()["review_job_id"]
        job = db_session.execute(select(ReviewJob)).scalar_one()
        assert job.status is ReviewJobStatus.QUEUED
        assert job.head_sha == "a" * 40

    def test_links_the_job_to_the_delivery_that_caused_it(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        """Lets a review be traced back to the exact delivery that triggered it."""
        post_webhook(client, pull_request_payload())

        job = db_session.execute(select(ReviewJob)).scalar_one()
        event = db_session.execute(select(WebhookEvent)).scalar_one()
        assert job.webhook_event_id == event.id

    @pytest.mark.parametrize("action", ["opened", "reopened", "synchronize", "ready_for_review"])
    def test_reviewable_actions_queue_work(
        self,
        client: TestClient,
        db_session: Session,
        tracked_repository: Repository,
        action: str,
    ) -> None:
        response = post_webhook(client, pull_request_payload(action=action))

        assert response.status_code == 202

    @pytest.mark.parametrize("action", ["edited", "labeled", "assigned", "closed"])
    def test_non_code_actions_do_not_queue_work(
        self,
        client: TestClient,
        db_session: Session,
        tracked_repository: Repository,
        action: str,
    ) -> None:
        """`edited` fires for title and description changes, which alter no code."""
        response = post_webhook(client, pull_request_payload(action=action))

        assert response.status_code == 200
        assert db_session.execute(select(ReviewJob)).first() is None

    def test_a_draft_is_not_reviewed(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        """A draft is explicitly not ready; reviewing it burns an LLM call on
        code the author is still writing."""
        response = post_webhook(client, pull_request_payload(draft=True))

        assert response.status_code == 200
        assert db_session.execute(select(ReviewJob)).first() is None

    def test_a_merged_pull_request_is_recorded_as_merged_not_closed(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        """GitHub reports a merge as `closed` with `merged: true`; without
        combining them a merge is indistinguishable from an abandonment."""
        post_webhook(client, pull_request_payload(action="closed", state="closed", merged=True))

        stored = db_session.execute(select(PullRequest)).scalar_one()
        assert stored.state is PullRequestState.MERGED

    def test_updates_an_existing_pull_request_rather_than_duplicating(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        post_webhook(client, pull_request_payload(action="opened"))

        post_webhook(client, pull_request_payload(action="synchronize", head_sha="c" * 40))

        stored = db_session.execute(select(PullRequest)).scalars().all()
        assert len(stored) == 1
        assert stored[0].head_sha == "c" * 40


class TestSupersededJobs:
    def test_a_new_push_cancels_the_review_of_the_old_commit(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        """The old diff is no longer what anyone will merge, so reviewing it
        spends an LLM call on a version that is already gone."""
        post_webhook(client, pull_request_payload(head_sha="a" * 40))

        post_webhook(client, pull_request_payload(action="synchronize", head_sha="c" * 40))

        jobs = db_session.execute(select(ReviewJob).order_by(ReviewJob.created_at)).scalars().all()
        assert len(jobs) == 2
        assert jobs[0].status is ReviewJobStatus.CANCELLED
        assert jobs[1].status is ReviewJobStatus.QUEUED
        # The cancelled row survives for the audit trail.
        assert jobs[0].head_sha == "a" * 40

    def test_two_deliveries_for_the_same_commit_queue_one_review(
        self, client: TestClient, db_session: Session, tracked_repository: Repository
    ) -> None:
        """`synchronize` and `ready_for_review` can arrive together without the
        head moving; only one review is warranted."""
        post_webhook(client, pull_request_payload(action="opened", head_sha="a" * 40))

        second = post_webhook(
            client, pull_request_payload(action="ready_for_review", head_sha="a" * 40)
        )

        assert second.status_code == 200
        assert second.json()["review_job_id"] is None
        jobs = db_session.execute(select(ReviewJob)).scalars().all()
        assert len(jobs) == 1


class TestSecretNotConfigured:
    def test_every_delivery_is_refused_without_a_secret(
        self, db_session: Session, settings: Settings
    ) -> None:
        """The default must be to refuse: accepting unverified webhooks would let
        anyone who knows the URL create review jobs."""
        from collections.abc import Iterator

        from app.api.deps import get_db

        application = create_app(settings.model_copy(update={"environment": Environment.CI}))

        def override_get_db() -> Iterator[Session]:
            yield db_session

        application.dependency_overrides[get_db] = override_get_db

        with TestClient(application) as unsecured_client:
            response = post_webhook(unsecured_client, pull_request_payload())

        assert response.status_code == 401
