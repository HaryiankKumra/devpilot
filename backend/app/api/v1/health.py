"""Liveness and readiness endpoints.

The split matters to an orchestrator: a failing *liveness* probe means "restart
this container", while a failing *readiness* probe means "stop routing traffic
here, but leave it alone -- it is probably waiting on Postgres". Restarting a
process because its database is briefly unavailable makes an outage worse, so
liveness deliberately touches nothing.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import AppSettings, DbSession, RedisClient
from app.schemas.health import HealthStatus, LivenessResponse, ReadinessResponse
from app.services import health as health_service

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=LivenessResponse,
    summary="Liveness probe",
)
def liveness(settings: AppSettings) -> LivenessResponse:
    """Return 200 whenever the process is able to answer. Touches no dependency."""
    return LivenessResponse(
        status=HealthStatus.OK,
        environment=settings.environment.value,
        version=settings.version,
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"description": "At least one backing service is unavailable."}},
)
def readiness(
    session: DbSession, redis_client: RedisClient, response: Response
) -> ReadinessResponse:
    """Probe PostgreSQL and Redis; answer 503 if either is unreachable."""
    result = health_service.check_readiness(session, redis_client)
    if result.status is not HealthStatus.OK:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
