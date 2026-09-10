# DevPilot

AI-powered pull request review for GitHub repositories.

DevPilot installs as a GitHub App, listens for pull request webhooks, and runs
each PR through a review pipeline: fetch the diff, run static analysis, retrieve
relevant repository context with semantic search, ask an LLM for a strictly
validated structured review, score the risk deterministically, and post the
high-confidence findings back to the pull request.

> **Status: Milestone 4 of 12 complete.** Accounts, the GitHub App integration
> and verified webhook ingestion all work: a pull request on a connected
> repository is recorded and a review job is queued. Nothing processes that
> queue yet — the worker arrives in Milestone 5 — and no review is produced.
> Pages that are routed but not built say so explicitly rather than showing
> placeholder data.
>
> **It runs with no GitHub credentials.** `DEVPILOT_GITHUB_MODE=mock` (the
> default) serves the GitHub API from an in-process fake, so the whole
> application works out of the box. See
> [`docs/github-app-setup.md`](docs/github-app-setup.md) to connect real GitHub.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Quick start (Docker)](#quick-start-docker)
- [Running without Docker](#running-without-docker)
- [Environment variables](#environment-variables)
- [API](#api)
- [Database](#database)
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
| `DEVPILOT_SECRET_KEY`   | Signs access tokens; production refuses to start on the placeholder |
| `DEVPILOT_ACCESS_TOKEN_EXPIRE_MINUTES` | Access-token lifetime     |
| `DEVPILOT_DATABASE_URL` | PostgreSQL connection URL                |
| `DEVPILOT_REDIS_URL`    | Redis connection URL                     |
| `DEVPILOT_CORS_ORIGINS` | Comma-separated allowed browser origins  |
| `DEVPILOT_GITHUB_MODE`  | `mock` (no credentials) or `live`        |
| `DEVPILOT_GITHUB_APP_ID`, `..._PRIVATE_KEY_PATH` | GitHub App identity |
| `DEVPILOT_GITHUB_WEBHOOK_SECRET` | Verifies incoming webhooks      |
| `DEVPILOT_GITHUB_CLIENT_ID`, `..._CLIENT_SECRET` | OAuth account linking |
| `VITE_API_BASE_URL`     | API URL the browser should call          |

## API

Interactive documentation is served at `/docs` outside production. Current
endpoints:

| Method | Path                    | Purpose                              |
| ------ | ----------------------- | ------------------------------------ |
| GET    | `/health`               | Liveness; touches no dependency      |
| GET    | `/health/ready`         | Readiness; probes Postgres and Redis |
| POST   | `/api/v1/auth/register` | Create an account                    |
| POST   | `/api/v1/auth/login`    | Exchange credentials for a token     |
| GET    | `/api/v1/auth/me`       | The authenticated user               |
| GET    | `/api/v1/github/status` | Whether a GitHub account is linked   |
| GET    | `/api/v1/github/authorize` | Begin GitHub OAuth linking        |
| GET    | `/api/v1/github/callback`  | GitHub OAuth callback             |
| DELETE | `/api/v1/github/link`   | Unlink the GitHub account            |
| GET    | `/api/v1/repositories`  | List tracked repositories            |
| GET    | `/api/v1/repositories/installations` | GitHub App installations|
| POST   | `/api/v1/repositories/sync` | Reconcile with an installation   |
| GET    | `/api/v1/repositories/{id}` | One repository                   |
| GET    | `/api/v1/repositories/{id}/pull-requests` | Its pull requests  |
| POST   | `/api/v1/webhooks/github` | Receive a GitHub delivery          |

Errors always use one envelope, so clients parse a single shape:

```json
{ "error": { "code": "invalid_credentials", "message": "Incorrect email or password." } }
```

Authenticated requests carry `Authorization: Bearer <token>`.

## Database

Eight tables: `users`, `repositories`, `pull_requests`, `webhook_events`,
`review_jobs`, `reviews`, `findings`, `code_chunks`. The schema and the
reasoning behind it are described in
[`docs/architecture.md`](docs/architecture.md#data-model).

Schema changes are applied only through Alembic; the application never creates
tables at startup.

```bash
cd backend
alembic upgrade head            # apply migrations
alembic upgrade head --sql      # review the SQL without connecting
alembic revision --autogenerate -m "describe the change"
alembic downgrade -1            # roll back one migration
```

Under Docker Compose, run them inside the API container:

```bash
docker compose exec api alembic upgrade head
```

`code_chunks` needs the pgvector extension, which is why it lives in its own
migration (`0002`) rather than the initial one.

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

The suite runs with no services installed. Application tests use an in-memory
SQLite database, and `tests/test_migrations.py` renders the Alembic migrations
to PostgreSQL DDL offline and asserts they still match the ORM models — which is
what catches the "edited a model, forgot the migration" drift that SQLite-based
tests would otherwise hide.

Tests that genuinely need PostgreSQL and Redis (pgvector similarity search,
above all) are marked `integration` and can be excluded with
`-m "not integration"`.

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
| 2   | Authentication and database models                   | Done   |
| 3   | GitHub App integration and repository management     | Done   |
| 4   | Webhook ingestion and idempotency                    | Done   |
| 5   | Celery workers and asynchronous review jobs          | Next   |
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
- [`docs/github-app-setup.md`](docs/github-app-setup.md) — connecting a real GitHub App

## License

MIT
