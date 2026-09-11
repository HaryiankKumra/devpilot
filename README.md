# DevPilot

AI-powered pull request review for GitHub repositories.

DevPilot installs as a GitHub App, listens for pull request webhooks, and runs
each PR through a review pipeline: fetch the diff, run static analysis, retrieve
relevant repository context with semantic search, ask an LLM for a strictly
validated structured review, score the risk deterministically, and post the
high-confidence findings back to the pull request.

> **Status: complete — all 12 milestones.** The pipeline runs end to end: a
> webhook is verified and recorded, a worker claims the job, fetches the diff,
> runs static analysis, retrieves related code from pgvector, asks a language
> model for a structured review, discards every finding the diff does not
> support, scores the risk in Python, stores everything, and posts the
> high-confidence findings back to the pull request as a single review.
>
> **It runs with no credentials at all.** `DEVPILOT_GITHUB_MODE=mock` and
> `DEVPILOT_LLM_MODE=mock` are the defaults, so the whole thing works out of the
> box and costs nothing. A mocked review labels itself `[Mock review — no
> language model was called.]` and its findings sit below the posting threshold,
> so it can never be mistaken for the real thing. See
> [`docs/llm-setup.md`](docs/llm-setup.md) and
> [`docs/github-app-setup.md`](docs/github-app-setup.md).
>
> **And it runs for free on real reviews too.** `DEVPILOT_LLM_MODE=gemini` uses
> Google's free tier, rotating across however many API keys you give it and
> backing off per key when one is throttled. `anthropic` is the paid
> alternative. Both sit behind the same `LLMProvider` Protocol, so the prompt,
> the schema validation and the risk score are identical either way — switching
> vendor is one environment variable.
>
> **Verified, not assumed:** 528 backend tests, 10 Playwright tests driving a
> browser against the real Docker stack, `ruff` and `mypy --strict` clean across
> 120 source files, and a Locust run of 644 requests with **0 failures**
> (P50 15 ms, P95 240 ms, P99 350 ms). Production configuration, CI and
> deployment docs are in place — see
> [`docs/deployment.md`](docs/deployment.md).

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
- [Continuous integration](#continuous-integration)
- [Production deployment](#production-deployment)
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
| Analysis  | Ruff (parser-based, never executes reviewed code)                   |
| AI        | Gemini (free tier) or Claude, behind one Protocol; Pydantic-validated structured output |

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
docker compose logs -f api      # follow API logs
docker compose logs -f worker   # follow the review worker
docker compose down             # stop
docker compose down -v          # stop and delete database volumes
```

Five services run: `postgres`, `redis`, `api`, `worker` and `frontend`. The
worker shares the API's image so the two can never drift apart in the code they
import.

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
| `DEVPILOT_LLM_MODE`     | `mock`, `gemini` (free tier) or `anthropic` (paid) |
| `DEVPILOT_GEMINI_API_KEYS` | One or more keys, comma-separated; rotated round-robin |
| `DEVPILOT_ANTHROPIC_API_KEY` | Required for `anthropic` mode        |
| `DEVPILOT_LLM_MODEL`    | Blank follows the provider's default     |
| `VITE_API_BASE_URL`     | API URL the browser should call          |

## API

Interactive documentation is served at `/docs` outside production — it is
generated from the same Pydantic models the code validates against, so it cannot
drift. [`docs/api.md`](docs/api.md) explains what a schema cannot: why each
endpoint is shaped the way it is and what each status code means.

All twenty endpoints:

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
| POST   | `/api/v1/repositories/{id}/index` | Index for semantic search  |
| GET    | `/api/v1/repositories/{id}` | One repository                   |
| GET    | `/api/v1/repositories/{id}/pull-requests` | Its pull requests  |
| GET    | `/api/v1/reviews`       | Recent reviews (the dashboard feed)  |
| GET    | `/api/v1/reviews/{id}`  | One review with its findings         |
| GET    | `/api/v1/pull-requests/{id}` | Its jobs and results, failures included |
| GET    | `/api/v1/findings`      | Findings across every review         |
| POST   | `/api/v1/webhooks/github` | Receive a GitHub delivery          |

Errors always use one envelope, so clients parse a single shape:

```json
{ "error": { "code": "invalid_credentials", "message": "Incorrect email or password." } }
```

Authenticated requests carry `Authorization: Bearer <token>`.

## Database

Eight tables: `users`, `repositories`, `pull_requests`, `webhook_events`,
`review_jobs`, `reviews`, `findings`, `code_chunks`. Every column, constraint and
index — and why each one is there — is in
[`docs/database-schema.md`](docs/database-schema.md); the boundaries they sit
inside are in [`docs/architecture.md`](docs/architecture.md#data-model).

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

Tests that genuinely need PostgreSQL — pgvector similarity search above all —
are marked `integration`. They run against the Compose database inside a
transaction that is rolled back, so nothing they write survives, and they skip
cleanly when it is not running:

```bash
../.venv/Scripts/python -m pytest -m "not integration"   # no services needed
```

### End-to-end tests

Playwright drives a real browser against the running Compose stack — no mocked
network layer. A mocked API only proves the frontend renders what the frontend
expected; every bug these actually caught lived in the seam between the layers.

```bash
docker compose up -d
echo "DEVPILOT_RATE_LIMIT_AUTH_REQUESTS=500" >> .env && docker compose up -d api
cd frontend && npx playwright install chromium && npm run test:e2e
```

That second line is not incidental. Every test registers its own account so the
tests cannot interfere with each other, and they all arrive from one IP — which
trips DevPilot's own auth limiter at 10 attempts/minute. The limit is the right
production default and the wrong one for a test runner, so the e2e stack gets a
bigger budget. It is **raised, not disabled**, so the middleware stays in the
request path and a limiter that miscounted would still fail the suite.

### Load tests

Three traffic profiles — dashboard reads, GitHub webhook deliveries, and logins
— weighted the way real traffic is shaped. A 429 counts as a **success**, because
the rate limiter refusing a request is the system working; counting those as
failures would measure how hard you pushed rather than whether anything broke.

```bash
pip install locust
locust -f load/locustfile.py --host http://localhost:8000 \
    --users 50 --spawn-rate 5 --run-time 2m --headless --html load/report.html
```

Last measured run, 50 concurrent users against the local Compose stack:

| Metric | Value |
| ------ | ----- |
| Requests | 644 |
| Failures | **0** |
| P50 latency | 15 ms |
| P95 latency | 240 ms |
| P99 latency | 350 ms |

The first run of this was not clean: it surfaced two HTTP 500s from a
check-then-insert race on `pull_requests`, and a globally unique constraint that
broke as soon as four accounts tracked one repository. Both are fixed
(migrations `0006`/`0007`); both were invisible to a passing unit suite, because
they need concurrency and more than one tenant to appear.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to
`main` and every pull request, in four jobs:

| Job | What it does |
| --- | ------------ |
| **Backend** | ruff, ruff format, `mypy --strict`, then `alembic upgrade head` and the full pytest suite against real Postgres (pgvector) and Redis services |
| **Frontend** | `npm ci`, lint, format check, production build |
| **End to end** | Boots the Compose stack, waits for `/health`, runs Playwright; uploads the report and service logs on failure |
| **Docker images** | Builds the production API and frontend images — the dev Compose images use a different target, so nothing else proves these still build |

Migrations run against a real database rather than being applied from model
metadata: CI is the only place they execute before a deploy does.

Unit jobs gate the slower ones — a type error should fail in seconds, not after a
browser download.

## Production deployment

```bash
cp .env.example .env.prod          # then fill it in properly
docker compose -f docker-compose.prod.yml up -d --build
```

[`docker-compose.prod.yml`](docker-compose.prod.yml) is a separate file, not an
override on the dev one: an override can add and replace keys but never remove
them, so the development bind mounts and `--reload` would survive into
production. No source mounts, no reloader, memory and CPU limits on everything,
migrations as a one-shot job that must exit 0 before the API starts, and
**Caddy terminating TLS inside the stack** — set `DEVPILOT_DOMAIN` and it gets
its own Let's Encrypt certificate. Nothing but ports 80 and 443 is published.

It runs for free on an Oracle Cloud Always Free VM; there is a
[step-by-step walkthrough](docs/deployment.md#walkthrough-oracle-cloud-always-free)
and a [bootstrap script](deploy/bootstrap.sh) that prepares a fresh Ubuntu host.

The application **refuses to boot in production** while `DEVPILOT_SECRET_KEY` is
still the development placeholder. Full walkthrough and pre-deploy checklist:
[`docs/deployment.md`](docs/deployment.md).

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
  e2e/            Playwright specs, run against the real stack
load/             Locust profiles
docs/             architecture, tradeoffs, API, schema, deployment
.github/workflows/ CI pipeline
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
| 5   | Celery workers and asynchronous review jobs          | Done   |
| 6   | PR diff retrieval and static analysis                | Done   |
| 7   | LLM integration with structured output validation    | Done   |
| 8   | Repository indexing and pgvector RAG                 | Done   |
| 9   | Full review pipeline and GitHub comments             | Done   |
| 10  | React dashboard and review visualisation             | Done   |
| 11  | Testing, security hardening, rate limiting, retries  | Done   |
| 12  | Production Docker, CI/CD, deployment, documentation  | Done   |

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — components and boundaries
- [`docs/engineering-tradeoffs.md`](docs/engineering-tradeoffs.md) — 107 decisions, their alternatives, and what each one costs
- [`docs/api.md`](docs/api.md) — endpoint reference and why each one is shaped that way
- [`docs/database-schema.md`](docs/database-schema.md) — the eight tables, their constraints, and the reasoning
- [`docs/deployment.md`](docs/deployment.md) — running it in production, and the checklist before you do
- [`docs/github-app-setup.md`](docs/github-app-setup.md) — connecting a real GitHub App
- [`docs/llm-setup.md`](docs/llm-setup.md) — connecting a real language model, and what it costs

## License

MIT
