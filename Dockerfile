# syntax=docker/dockerfile:1

# =============================================================================
# DevPilot, all in one container.
#
# For hosts that give you exactly one free web service -- Render, Hugging Face
# Spaces -- rather than a machine. The API, the Celery worker and the built
# frontend run in a single container; PostgreSQL and Redis are external
# (Neon and Render Key Value on the free path).
#
# It lives at the repository root because that is where every Docker PaaS looks
# by default -- Render, Railway, Koyeb, Fly -- and a service created by hand in
# any of their dashboards fails with "no such file: Dockerfile" otherwise. The
# Compose stacks use backend/Dockerfile and frontend/Dockerfile instead.
#
#   docker build -t devpilot .
#
# This is a deliberate compromise and the Compose files remain the reference
# deployment. Two processes in one container means one restart unit, one set
# of resource limits, and no way to scale the worker separately -- all fine at
# the size a free tier allows, and exactly the things to change when it grows.
# =============================================================================

# --- Stage 1: frontend bundle -----------------------------------------------
FROM node:22-alpine AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# `/` means "same origin": the API serves this bundle, so the browser calls the
# host it loaded from and nothing has to be rebuilt when the hostname changes.
ENV VITE_API_BASE_URL=/
RUN npm run build

# --- Stage 2: Python dependencies -------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# --- Stage 3: runtime --------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    # Where the API finds the bundle; see app/api/spa.py.
    DEVPILOT_STATIC_DIR=/app/static \
    # Free-tier hosts hand out the port; default matches Render's convention.
    PORT=10000

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 devpilot

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=devpilot:devpilot backend/app ./app
COPY --chown=devpilot:devpilot backend/alembic.ini ./alembic.ini
COPY --chown=devpilot:devpilot backend/alembic ./alembic
COPY --chown=devpilot:devpilot backend/scripts ./scripts
COPY --from=frontend --chown=devpilot:devpilot /build/dist ./static
COPY --chown=devpilot:devpilot deploy/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

USER devpilot
EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

ENTRYPOINT ["./entrypoint.sh"]
