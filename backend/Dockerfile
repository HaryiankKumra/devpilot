# syntax=docker/dockerfile:1

# =============================================================================
# DevPilot API image.
#
# Multi-stage: the builder compiles wheels (some dependencies need a C
# toolchain), and the runtime stage copies only the installed packages. That
# keeps the final image small and free of compilers, which is both faster to
# pull and a smaller attack surface.
# =============================================================================

# --- Stage 1: build dependencies --------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy only the dependency manifest first: this layer is cached and rebuilt
# only when dependencies actually change, not on every source edit.
COPY pyproject.toml ./
COPY app ./app

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# --- Stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# curl is used by the container healthcheck below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    # Run as an unprivileged user: a container escape should not land as root.
    && useradd --create-home --uid 10001 devpilot

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=devpilot:devpilot app ./app

USER devpilot

EXPOSE 8000

# Liveness only: readiness depends on Postgres and Redis, and Docker would
# restart this container for their outage if we probed it here.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
