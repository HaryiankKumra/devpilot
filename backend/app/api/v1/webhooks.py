"""The GitHub webhook endpoint.

This is the only unauthenticated write endpoint in DevPilot, so the order of
operations is the security design:

1. Read the **raw body**. Not a parsed model -- the signature covers the exact
   bytes GitHub sent, and re-serialised JSON is different bytes.
2. Verify the signature. An unsigned or wrongly-signed request is rejected here
   and never reaches the database.
3. Only then parse.

The route deliberately does not use FastAPI's request-model binding, which would
parse the body before any of this could run. That costs some brevity and buys
the only thing separating GitHub from anyone who guessed the URL.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.api.deps import AppSettings, DbSession
from app.core.enums import WebhookEventStatus
from app.core.exceptions import DevPilotError
from app.core.logging import get_logger
from app.db.repositories.review_job import ReviewJobStore
from app.integrations.github.webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    InvalidSignatureError,
    verify_signature,
)
from app.schemas.webhook import WebhookAck
from app.services import webhooks as webhook_service
from app.services.dispatch import dispatch_review_job
from app.services.webhooks import DuplicateDeliveryError

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# GitHub's own limit is 25 MB. Refusing anything larger before reading it all
# stops a forged request from making the API buffer arbitrary memory -- and the
# check is free, since the signature has not been verified at that point either.
MAX_PAYLOAD_BYTES = 25 * 1024 * 1024


class WebhookRejected(DevPilotError):
    """The request did not come from GitHub, or could not be understood."""

    def __init__(self, message: str, status_code: int, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@router.post(
    "/github",
    response_model=WebhookAck,
    summary="Receive a GitHub webhook delivery",
    responses={
        200: {"description": "Already handled, or deliberately ignored."},
        202: {"description": "Accepted; a review has been queued."},
        400: {"description": "Body is not valid JSON, or is too large."},
        401: {"description": "Missing or invalid signature."},
    },
)
async def receive_github_webhook(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> WebhookAck:
    """Verify, record and dispatch one delivery.

    Answers quickly in every case. Anything slow belongs on a worker, because a
    webhook that blocks is a webhook GitHub will time out and send again.
    """
    raw_body = await request.body()

    if len(raw_body) > MAX_PAYLOAD_BYTES:
        raise WebhookRejected(
            "Payload too large.", status.HTTP_400_BAD_REQUEST, "payload_too_large"
        )

    secret = (
        settings.github_webhook_secret.get_secret_value() if settings.github_webhook_secret else ""
    )
    try:
        verify_signature(raw_body, request.headers.get(SIGNATURE_HEADER), secret)
    except InvalidSignatureError as exc:
        # Logged at warning: a burst of these is either a misconfigured secret
        # or somebody probing the endpoint, and both are worth noticing.
        logger.warning(
            "webhook.signature_rejected",
            reason=str(exc),
            delivery_id=request.headers.get(DELIVERY_HEADER),
        )
        raise WebhookRejected(
            "Invalid signature.", status.HTTP_401_UNAUTHORIZED, "invalid_signature"
        ) from exc

    delivery_id = request.headers.get(DELIVERY_HEADER)
    event_type = request.headers.get(EVENT_HEADER)
    if not delivery_id or not event_type:
        # A signed request without these is not something GitHub sends.
        raise WebhookRejected(
            "Missing delivery or event headers.",
            status.HTTP_400_BAD_REQUEST,
            "malformed_webhook",
        )

    try:
        payload: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise WebhookRejected(
            "Body is not valid JSON.", status.HTTP_400_BAD_REQUEST, "malformed_webhook"
        ) from exc
    if not isinstance(payload, dict):
        raise WebhookRejected(
            "Body is not a JSON object.", status.HTTP_400_BAD_REQUEST, "malformed_webhook"
        )

    try:
        event = webhook_service.claim_delivery(
            session,
            delivery_id=delivery_id,
            event_type=event_type,
            action=payload.get("action"),
            payload=payload,
        )
    except DuplicateDeliveryError:
        # A retry of something already handled. Answering 2xx is what stops
        # GitHub retrying it forever.
        session.commit()
        return WebhookAck(
            status=WebhookEventStatus.PROCESSED,
            detail="This delivery has already been handled.",
            duplicate=True,
        )

    result = webhook_service.process_event(session, event=event)
    session.commit()

    # Dispatch only after the commit. Redis and PostgreSQL share no transaction,
    # so publishing first lets a worker look for a row that is not there yet --
    # or never lands at all. See app/services/dispatch.py.
    if result.review_job_id is not None:
        celery_task_id = dispatch_review_job(uuid.UUID(result.review_job_id))
        if celery_task_id is not None:
            _record_celery_task_id(session, result.review_job_id, celery_task_id)

    if result.accepted_work:
        response.status_code = status.HTTP_202_ACCEPTED

    return WebhookAck(
        status=result.status,
        detail=result.detail,
        duplicate=False,
        review_job_id=result.review_job_id,
    )


def _record_celery_task_id(session: Any, review_job_id: str, celery_task_id: str) -> None:
    """Store the broker's task id so a row can be traced to worker logs.

    Best-effort: the job is already queued and will run regardless, so a failure
    to annotate it must not turn a successful webhook into a 500.
    """
    job = ReviewJobStore(session).get_by_id(uuid.UUID(review_job_id))
    if job is None:
        return
    job.celery_task_id = celery_task_id
    session.commit()
