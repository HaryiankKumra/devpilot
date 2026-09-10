"""Response schema for the webhook endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.enums import WebhookEventStatus


class WebhookAck(BaseModel):
    """What DevPilot tells GitHub it did with a delivery.

    GitHub only really cares whether the response is 2xx -- anything else is
    treated as a failed delivery and retried. The body exists for the humans
    reading the delivery log on the App's settings page, where an explicit
    "ignored, because the repository is not tracked" saves a long debugging
    session.
    """

    status: WebhookEventStatus
    detail: str = Field(description="Human-readable explanation of the outcome.")
    duplicate: bool = Field(
        default=False,
        description="True when this delivery id had already been recorded.",
    )
    review_job_id: str | None = Field(default=None, description="The review job created, if any.")
