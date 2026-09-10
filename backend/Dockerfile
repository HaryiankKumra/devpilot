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

# No compiler is installed here on purpose. Every dependency ships a prebuilt
# manylinux wheel for CPython 3.12 -- `psycopg[binary]` bundles libpq, and
# cryptography and argon2-cffi publish wheels too -- so apt-installing
# build-essential cost roughly nine minutes of build time and produced nothing.
# If a future dependency genuinely needs to compile, pip fails loudly and the
# toolchain can be reinstated at this line.

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

# The migrations ship in the image so that `docker compose exec api alembic
# upgrade head` works against the exact code the container is running. Without
# them the image can serve traffic but cannot create the schema it needs.
COPY --chown=devpilot:devpilot alembic.ini ./alembic.ini
COPY --chown=devpilot:devpilot alembic ./alembic

USER devpilot

EXPOSE 8000

# Liveness only: readiness depends on Postgres and Redis, and Docker would
# restart this container for their outage if we probed it here.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
