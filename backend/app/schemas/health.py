"""Schemas for the health and readiness endpoints."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class HealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"


class LivenessResponse(BaseModel):
    """Answer to "is the process running?"."""

    status: HealthStatus = Field(description="Always `ok` if the process can respond.")
    environment: str = Field(description="Deployment environment name.")
    version: str = Field(description="Application version.")


class DependencyCheck(BaseModel):
    """Result of probing one backing service."""

    name: str = Field(description="Dependency name, e.g. `postgres`.")
    healthy: bool = Field(description="True when the dependency answered successfully.")
    detail: str | None = Field(
        default=None,
        description="Error summary when unhealthy; omitted when healthy.",
    )
    latency_ms: float | None = Field(
        default=None, description="Round-trip time of the probe in milliseconds."
    )


class ReadinessResponse(BaseModel):
    """Answer to "can this process serve traffic?"."""

    status: HealthStatus
    dependencies: list[DependencyCheck]
