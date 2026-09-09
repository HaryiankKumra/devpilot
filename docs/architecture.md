# Architecture

This document explains how DevPilot is put together and, more importantly, *why*
each boundary is where it is. It is written to be defensible in an interview:
every section should let you answer "why did you do it that way?".

- [System overview](#system-overview)
- [The review pipeline](#the-review-pipeline)
- [Backend layering](#backend-layering)
- [Data model](#data-model)
- [Frontend structure](#frontend-structure)
- [Cross-cutting concerns](#cross-cutting-concerns)
- [Failure handling](#failure-handling)
- [Implementation status](#implementation-status)

---

## System overview

DevPilot is a **modular monolith with an asynchronous worker**, not a set of
microservices. There are two deployable processes that share one codebase and
one database:

| Process    | Responsibility                                                     |
| ---------- | ------------------------------------------------------------------ |
| API        | Serve REST requests, verify and record webhooks, enqueue jobs       |
| Worker     | Run the review pipeline: GitHub, static analysis, retrieval, LLM    |

They are split along the only boundary that actually matters here: **request
latency**. An HTTP request must answer in milliseconds; a review involves
several network round trips and can take minutes. Splitting further — a separate
"embedding service", an "LLM gateway" — would add network hops, deployment units
and failure modes without removing any coupling, because all of those pieces
read and write the same rows.

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

## The review pipeline

The webhook path is deliberately split into a fast synchronous half and a slow
asynchronous half.

**Synchronously, inside the webhook request (target: tens of milliseconds):**

1. Read the raw request body — *before* parsing it as JSON.
2. Verify the `X-Hub-Signature-256` HMAC against the raw bytes. An unsigned or
   mis-signed request is rejected here and never touches the database.
3. Check `X-GitHub-Delivery` against the `webhook_events` table. GitHub retries
   deliveries, so a repeat delivery id must be a no-op that still returns 2xx.
4. Persist the event, create a `review_jobs` row in `queued`, enqueue the job id
   on Redis, and return `202 Accepted`.

**Asynchronously, on the worker (seconds to minutes):**

5. Fetch the PR diff from the GitHub API.
6. Run static analysis on the changed files where a tool applies.
7. Retrieve related code from `code_chunks` by vector similarity (pgvector).
8. Build a prompt from the diff, the static-analysis output and the retrieved
   context.
9. Call the LLM and parse the response into a Pydantic model. Invalid output is
   rejected and retried, never stored.
10. Compute the risk score in Python from the validated findings.
11. Persist the review and findings.
12. Post findings above the confidence threshold back to the pull request.

Two properties of this split are worth stating explicitly, because they are the
reason it is shaped this way:

- **The webhook endpoint does no work that can fail slowly.** If the LLM is down,
  GitHub still gets its `202`, and the job simply retries later. Doing the review
  inline would mean a webhook timeout, which GitHub interprets as a failed
  delivery and retries — turning one slow review into several.
- **Verification precedes parsing.** The signature is computed over the exact
  bytes received. Parsing JSON first and re-serialising it to verify would be a
  real vulnerability, because the re-serialised bytes are not the signed bytes.

## Backend layering

```
app/api/       ← HTTP: routing, status codes, dependency injection
app/services/  ← business logic
app/db/        ← engine, session, ORM models, data access
app/schemas/   ← Pydantic models for API input and output
app/core/      ← config, logging, middleware, exceptions
```

The dependency rule points one way only:

```
api ──▶ services ──▶ db
 │          │
 └──────────┴──▶ schemas, core
```

**Nothing in `services/` imports FastAPI.** That is the constraint that makes the
layering real rather than decorative. It means business logic can be tested by
calling a function with a session and asserting on the result — no HTTP client,
no ASGI app, no route table. It also means the same service functions are called
by Celery tasks, which have no HTTP request at all.

Routes stay thin: parse and validate input via a Pydantic schema, call one
service function, return its result. Status-code selection is the route's job;
deciding *what happened* is the service's job. Services signal outcomes by
raising domain exceptions (`NotFoundError`, `ConflictError`) from
`app/core/exceptions.py`, and a single set of registered handlers maps those to
HTTP responses. Adding a route therefore cannot invent a new error shape.

### Why synchronous SQLAlchemy

FastAPI is an async framework, so async SQLAlchemy is the obvious default. This
project uses the **synchronous** API everywhere instead, for one dominant reason:
Celery workers are synchronous processes, and the API and the workers share the
same data-access code. Going async in the API would mean either maintaining two
parallel sets of repositories, or running an event loop inside Celery tasks.

The cost is that database calls block a thread — FastAPI runs `def` endpoints in
a thread pool, so they do not block the event loop. That cost is acceptable here
because the API is I/O-light by design: the expensive work is on the worker. If
the API ever became the bottleneck, the fix is more processes, not a rewrite.

## Data model

Eight tables, in dependency order:

| Table            | Purpose                                                       |
| ---------------- | ------------------------------------------------------------- |
| `users`          | Local accounts and their GitHub linkage                       |
| `repositories`   | Repositories where the GitHub App is installed                 |
| `pull_requests`  | Pull requests seen via webhooks                                |
| `webhook_events` | One row per GitHub delivery id — the idempotency ledger        |
| `review_jobs`    | Queued/running/failed/succeeded work, with error detail        |
| `reviews`        | One completed review: summary and computed risk score          |
| `findings`       | Individual issues, with file, line, severity and confidence    |
| `code_chunks`    | Embedded repository content for retrieval, with path and lines |

Two design points carry most of the weight:

- **`webhook_events` exists purely for idempotency.** A unique constraint on the
  GitHub delivery id turns "did we already handle this?" into a database
  guarantee rather than an application-level race. Two concurrent retries of the
  same delivery cannot both proceed, because the second insert violates the
  constraint.
- **`review_jobs` is separate from `reviews`.** A job is an *attempt*; a review
  is a *result*. Attempts fail, retry and record errors; results do not. Merging
  them would mean a failed review is indistinguishable from a review that found
  nothing.

`code_chunks` stores the file path and the start and end line of every chunk
alongside its embedding. Without that metadata, a retrieved chunk is a wall of
text the LLM cannot cite, and findings cannot be anchored to a line for posting
back to GitHub.

Schema changes are applied exclusively through Alembic migrations. The
application never creates tables at startup, so the schema in production is
always the result of a reviewed, version-controlled migration.

## Frontend structure

```
src/
  components/   shared, presentational UI
  features/     one folder per domain area: API bindings + hooks
  lib/          API client, React Query client, env validation
  pages/        one component per route
  routes/       the route table
```

The split that matters is `features/` versus `pages/`. A page is a *route target*
— it composes; it does not fetch. A feature owns its API binding and its React
Query hooks, so data fetching for reviews lives in one place regardless of how
many pages display reviews.

**Server state is React Query's job; it is never copied into component state.**
Caching, deduplication, retry and background refresh are already handled there,
and mirroring that data into `useState` reintroduces exactly the staleness bugs
the library exists to prevent.

`lib/apiClient.ts` normalises failures. The API always reports errors as
`{"error": {"code", "message"}}`, so the client parses that shape once and
raises a typed `ApiError`. React Query then retries only transient failures — a
404 or a 401 returns the same answer however many times you ask, so retrying it
just fails more slowly.

## Cross-cutting concerns

**Configuration.** All configuration is read once into a frozen, typed `Settings`
object. Nothing reads `os.environ` directly. Secrets have no working defaults, so
a missing secret fails at startup rather than silently degrading — a
misconfigured webhook secret that defaults to `""` would accept every forged
request.

**Logging.** Every log line is a structured event. A middleware assigns each
request a `request_id`, binds it to a context variable, and echoes it in the
`X-Request-ID` response header. Because it is a context variable, service code
deep in the call stack logs the correlation id without ever being passed it. In
`local` the renderer is human-readable; elsewhere it is one JSON object per line
so a log aggregator can index fields without regex.

**Health probes.** `/health` and `/health/ready` are deliberately different.
Liveness touches no dependency and answers "is this process alive?" — a failure
means *restart me*. Readiness probes Postgres and Redis and answers "should I
receive traffic?" — a failure means *route around me*. Conflating them causes an
outage to escalate: a brief database blip would restart every API container,
losing in-flight requests and adding a cold start to an already-degraded system.

Both probes are bounded by explicit timeouts. This matters more than it sounds:
without a connect timeout, an unreachable database does not make readiness fail —
it makes readiness *hang*, waiting out the OS TCP retry budget while the
orchestrator learns nothing.

## Failure handling

The system assumes every external call fails eventually.

| Failure                       | Response                                                        |
| ----------------------------- | --------------------------------------------------------------- |
| Invalid webhook signature     | `401`, nothing persisted                                         |
| Duplicate webhook delivery    | `200`, no new job — the delivery id is already recorded          |
| GitHub API transient error    | Worker retries with exponential backoff                          |
| LLM returns malformed JSON    | Rejected by Pydantic, retried; never stored                      |
| LLM invents a risk score      | Ignored — the score is computed from validated findings          |
| Worker crashes mid-review     | Job stays claimable and is retried; failures record the error    |
| Postgres or Redis unavailable | Readiness returns `503`; liveness stays green so nothing restarts |

The risk score deserves emphasis because it is the clearest example of the
project's stance on LLM output. Severities are weighted (`critical=10`,
`high=7`, `medium=4`, `low=1`), summed, and normalised to 0–100 **in Python**.
The model contributes findings; it does not contribute arithmetic. The same
findings therefore always yield the same score, which is both testable and
explainable to a user who asks why their PR scored 72.

## Implementation status

This document describes the complete target design. Milestone 1 has built the
skeleton it hangs on: configuration, structured logging, request correlation,
error handling, health probes, the database and Redis layers, the frontend shell
and the container setup.

Not yet implemented: authentication, the ORM models and migrations, GitHub App
integration, webhook ingestion, Celery workers, static analysis, LLM integration,
pgvector retrieval, and the dashboard. See the roadmap in the
[README](../README.md#roadmap) for the milestone that delivers each.
