"""Aggregates the versioned business routers.

Health probes are deliberately *not* included here: they are mounted at the
application root (`/health`) because orchestrators and load balancers expect a
stable, unversioned probe path that outlives any `/api/v1` -> `/api/v2` move.
"""

from __future__ import annotations

from fastapi import APIRouter

api_router = APIRouter()
