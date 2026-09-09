"""The `webhook_events` table: the GitHub delivery ledger."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import WebhookEventStatus
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import JSONColumn, enum_column_type


class WebhookEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One GitHub webhook delivery.

    This table exists primarily to make webhook handling **idempotent**. GitHub
    retries a delivery it believes failed, and a retry must not produce a second
    review. The unique constraint on `delivery_id` turns that from an
    application-level check-then-act race into a guarantee the database
    enforces: under concurrent retries the second INSERT simply fails.

    Recording the payload also gives an audit trail of what GitHub actually
    sent, which is the only way to debug a delivery that behaved unexpectedly.
    """

    __tablename__ = "webhook_events"

    # `X-GitHub-Delivery`. Unique -- this single constraint is the idempotency
    # guarantee, so it must never be relaxed to a plain index.
    delivery_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    # `X-GitHub-Event`, e.g. `pull_request`, plus the payload's `action`.
    event_type: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    action: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Nullable: a delivery may reference a repository DevPilot has never seen,
    # and the event must still be recorded so the retry is recognised.
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), index=True, nullable=True
    )

    payload: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)

    status: Mapped[WebhookEventStatus] = mapped_column(
        enum_column_type(WebhookEventStatus),
        default=WebhookEventStatus.RECEIVED,
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<WebhookEvent delivery_id={self.delivery_id!r} status={self.status}>"
