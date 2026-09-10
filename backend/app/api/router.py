"""Aggregates the versioned business routers.

Health probes are deliberately *not* included here: they are mounted at the
application root (`/health`) because orchestrators and load balancers expect a
stable, unversioned probe path that outlives any `/api/v1` -> `/api/v2` move.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, github, repositories, reviews, webhooks

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(github.router)
api_router.include_router(repositories.router)
api_router.include_router(reviews.router)
api_router.include_router(webhooks.router)
