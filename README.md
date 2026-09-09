# DevPilot

AI-powered pull request review for GitHub repositories.

DevPilot installs as a GitHub App, listens for pull request webhooks, and runs
each PR through a review pipeline: fetch the diff, run static analysis, retrieve
relevant repository context with semantic search, ask an LLM for a strictly
validated structured review, score the risk deterministically, and post the
high-confidence findings back to the pull request.

> **Status: Milestone 1 of 12 complete.** The API, database, cache and frontend
> shell run end to end. Review functionality is not implemented yet — see
> [Roadmap](#roadmap). Pages that are routed but not built say so explicitly
> rather than showing placeholder data.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Quick start (Docker)](#quick-start-docker)
- [Running without Docker](#running-without-docker)
- [Environment variables](#environment-variables)
- [Testing and quality gates](#testing-and-quality-gates)
- [Project layout](#project-layout)
- [Roadmap](#roadmap)
- [Documentation](#documentation)

---

## Why this exists

Code review is the slowest part of most delivery pipelines, and the first pass is
often mechanical: unhandled errors, missing validation, obvious injection risks,
N+1 queries. DevPilot automates that first pass so human reviewers spend their
attention on design and intent.

The engineering interest is not the LLM call — it is everything around it:

- **Webhooks are hostile input.** They are unauthenticated until proven
  otherwise, and GitHub retries deliveries, so signature verification and
  idempotency are correctness requirements, not features.
- **LLMs are unreliable narrators.** Their output is parsed and validated
  against a Pydantic schema and rejected when it does not conform. The risk
  score is computed in Python from validated findings, never taken from the
  model, so the same findings always produce the same score.
- **Reviews are slow.** A PR review involves several network round trips, so it
  runs asynchronously on a worker with retries, and every failure is recorded
  with enough context to debug it.

## Architecture

```
                 ┌──────────────┐
   Browser ─────▶│ React (Vite) │
                 └──────┬───────┘
                        │ REST/JSON
                 ┌──────▼───────┐        ┌──────────────┐
  GitHub ───────▶│   FastAPI    │───────▶│  PostgreSQL  │
  webhooks       │    (API)     │        │  + pgvector  │
                 └──────┬───────┘        └──────▲───────┘
                        │ enqueue               │
                 ┌──────▼───────┐               │
                 │    Redis     │               │
                 │   (broker)   │               │
                 └──────┬───────┘               │
                        │ dequeue               │
                 ┌──────▼───────┐               │
                 │ Celery worker│───────────────┘
                 └──────┬───────┘
                        │
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
   GitHub API    Static analysis   LLM + embeddings
```

The API stays thin: it validates input, persists it, and enqueues work.
Everything slow or failure-prone happens on the worker, where a retry is cheap
and a failure does not become an HTTP 500 for a webhook GitHub will re-deliver.

See [`docs/architecture.md`](docs/architecture.md) for the reasoning behind each
boundary, and [`docs/engineering-tradeoffs.md`](docs/engineering-tradeoffs.md)
for the decisions that could reasonably have gone the other way.

## Tech stack

| Layer     | Choice                                                              |
| --------- | ------------------------------------------------------------------- |
| Frontend  | React 19, TypeScript, Vite, Tailwind CSS, React Query, React Router  |
| API       | Python 3.12, FastAPI, Pydantic v2                                   |
| Data      | PostgreSQL 16 + pgvector, SQLAlchemy 2.0, Alembic                   |
| Async     | Redis, Celery                                                       |
| Packaging | Docker, Docker Compose, GitHub Actions                              |
| Testing   | Pytest, Playwright, Locust                                          |

## Quick start (Docker)

Requires Docker Desktop (or Docker Engine) with Compose v2.

```bash
cp .env.example .env      # Windows PowerShell: copy .env.example .env
docker compose up --build
```

| Service           | URL                                |
| ----------------- | ---------------------------------- |
| Frontend          | http://localhost:5173              |
| API               | http://localhost:8000              |
| API documentation | http://localhost:8000/docs         |
| Liveness probe    | http://localhost:8000/health       |
| Readiness probe   | http://localhost:8000/health/ready |

Compose waits for the Postgres and Redis healthchecks before starting the API,
so the first request does not race database initialisation.

```bash
docker compose logs -f api     # follow API logs
docker compose down            # stop
docker compose down -v         # stop and delete database volumes
```

## Running without Docker

You need Python 3.12+, Node 20.12+, and PostgreSQL and Redis reachable from your
machine.

**Backend**

```bash
python -m venv .venv
.venv/Scripts/activate            # macOS/Linux: source .venv/bin/activate
pip install -e "backend[dev]"
cd backend
uvicorn app.main:app --reload --port 8000
```

**Frontend**

```bash
cd frontend
npm install
npm run dev                       # http://localhost:5173
```

Point `DEVPILOT_DATABASE_URL` and `DEVPILOT_REDIS_URL` at `localhost` rather
than the Compose service names when running this way.

## Environment variables

Every variable is documented inline in [`.env.example`](.env.example), which is
the single source of truth. Configuration is read once into a typed `Settings`
object ([`backend/app/core/config.py`](backend/app/core/config.py)); nothing in
the codebase reads `os.environ` directly, and no secret has a real default.

| Variable                | Purpose                                 |
| ----------------------- | --------------------------------------- |
| `DEVPILOT_ENVIRONMENT`  | `local`, `ci` or `production`            |
| `DEVPILOT_DEBUG`        | Verbose errors and human-readable logs   |
| `DEVPILOT_LOG_LEVEL`    | Standard Python log level                |
| `DEVPILOT_DATABASE_URL` | PostgreSQL connection URL                |
| `DEVPILOT_REDIS_URL`    | Redis connection URL                     |
| `DEVPILOT_CORS_ORIGINS` | Comma-separated allowed browser origins  |
| `VITE_API_BASE_URL`     | API URL the browser should call          |

## Testing and quality gates

The backend gate — all four must pass:

```bash
cd backend
../.venv/Scripts/python -m pytest
../.venv/Scripts/python -m ruff check .
../.venv/Scripts/python -m ruff format --check .
../.venv/Scripts/python -m mypy app tests
```

The frontend gate:

```bash
cd frontend
npm run lint
npm run format:check
npm run build          # includes a full TypeScript project build
```

Tests that need real PostgreSQL and Redis are marked `integration` and can be
excluded with `-m "not integration"`. Everything currently in the suite runs
with no services running at all.

## Project layout

```
backend/
  app/
    api/          HTTP routes and dependency wiring (thin)
    core/         config, logging, middleware, exceptions
    db/           engine, session, Redis client, ORM base
    schemas/      Pydantic request/response models
    services/     business logic (no FastAPI imports)
  tests/
frontend/
  src/
    components/   shared UI
    features/     feature-scoped API bindings and hooks
    lib/          API client, query client, env validation
    pages/        one component per route
    routes/       route table
docs/             architecture, tradeoffs, operations
```

The dependency rule is one-directional: `api` may import `services`, `services`
may import `db` and `schemas`, and nothing in `services` imports FastAPI. That is
what keeps business logic testable without HTTP.

## Roadmap

| #   | Milestone                                            | Status |
| --- | ---------------------------------------------------- | ------ |
| 1   | Repo setup, Compose, FastAPI, React, Postgres, Redis | Done   |
| 2   | Authentication and database models                   | Next   |
| 3   | GitHub App integration and repository management     |        |
| 4   | Webhook ingestion and idempotency                    |        |
| 5   | Celery workers and asynchronous review jobs          |        |
| 6   | PR diff retrieval and static analysis                |        |
| 7   | LLM integration with structured output validation    |        |
| 8   | Repository indexing and pgvector RAG                 |        |
| 9   | Full review pipeline and GitHub comments             |        |
| 10  | React dashboard and review visualisation             |        |
| 11  | Testing, security hardening, rate limiting, retries  |        |
| 12  | Production Docker, CI/CD, deployment, documentation  |        |

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — components and boundaries
- [`docs/engineering-tradeoffs.md`](docs/engineering-tradeoffs.md) — decisions and alternatives

## License

MIT
