"""ORM model registry.

Importing every model here serves two purposes: Alembic sees the complete
metadata when generating migrations, and SQLAlchemy can resolve the string class
names used in `relationship()` declarations, which are only looked up once every
class has been registered.
"""

from __future__ import annotations

from app.db.models.code_chunk import CodeChunk
from app.db.models.finding import Finding
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.models.review import Review
from app.db.models.review_job import ReviewJob
from app.db.models.user import User
from app.db.models.webhook_event import WebhookEvent

__all__ = [
    "CodeChunk",
    "Finding",
    "PullRequest",
    "Repository",
    "Review",
    "ReviewJob",
    "User",
    "WebhookEvent",
]
