# API reference

Twenty-two endpoints under `/api/v1`, plus two unversioned health probes.

The **generated** OpenAPI document is the authoritative contract — it is produced
from the same Pydantic models the code validates against, so it cannot drift.
With `DEVPILOT_DEBUG=true`:

- Swagger UI — <http://localhost:8000/docs>
- ReDoc — <http://localhost:8000/redoc>
- Raw schema — <http://localhost:8000/openapi.json>

`/docs` is **disabled in production**. It is a map of the attack surface, and
there is no reason to publish one.

This document explains the parts a schema cannot: why the endpoints are shaped
the way they are, and what each status code means.

---

## Conventions

### Authentication

Every endpoint marked **auth** requires a bearer token:

```
Authorization: Bearer <access_token>
```

Tokens are JWTs signed with HS256, with the algorithm **pinned at verification**.
Accepting whatever `alg` the token itself claims is how `alg: none` and
RS256→HS256 confusion attacks work; the decoder is told what to expect rather
than asked to guess.

Tokens **cannot be revoked** before they expire. That is the cost of stateless
auth — no session table, no lookup per request — and the mitigation is a short
lifetime (`DEVPILOT_ACCESS_TOKEN_EXPIRE_MINUTES`, default 60).

### Ownership

Every query is scoped to the signed-in user by joining back to
`repositories.owner_id`. There is no "admin sees everything" path.

Requesting a resource that belongs to someone else returns **404, not 403**.
A 403 confirms the resource exists, which is itself information the caller has
not earned.

### Errors

Integration failures return a consistent envelope:

```json
{ "error": { "code": "github_rate_limited", "message": "..." } }
```

Validation failures return FastAPI's standard 422 body, which names the offending
field and why it was rejected.

The status code says **whose problem it is**:

| Status | Meaning |
|---|---|
| `400` | The request is malformed |
| `401` | Missing, expired or invalid credentials |
| `404` | No such resource *for this user* |
| `409` | Conflicts with existing state (e.g. the email is taken) |
| `422` | Well-formed but failed validation |
| `429` | Rate limited — `Retry-After` says how long |
| `502` | DevPilot could not get a usable answer out of GitHub |
| `503` | A dependency is unavailable, or GitHub is not configured |

Error messages never contain secret values. A configuration error names the
environment variable that is missing; it does not print what is in it.

### Rate limiting

Every response carries `X-RateLimit-Limit` and `X-RateLimit-Remaining`, so a
client can slow down *before* it is refused rather than after. A 429 adds
`Retry-After`.

Two budgets: 10 requests/minute on `/auth/login` and `/auth/register`, 300/minute
everywhere else. Auth is tighter because it is where credential stuffing happens
and because Argon2 makes each attempt expensive for the server too.

Webhooks and health probes are **never** limited — see tradeoff 77.

---

## Health

### `GET /health` — liveness

```json
{ "status": "ok", "environment": "production", "version": "0.1.0" }
```

Always 200 if the process can answer. It checks **nothing else**, deliberately:
an orchestrator restarts a container that fails liveness, and restarting the API
because Postgres is down turns one outage into a restart loop.

Unversioned, because probe URLs outlive `/api/v1`.

### `GET /health/ready` — readiness

200 when Postgres and Redis both answer, **503** otherwise, with a per-dependency
breakdown:

```json
{
  "status": "degraded",
  "dependencies": [
    { "name": "postgres", "healthy": false, "detail": "connection refused", "latency_ms": null },
    { "name": "redis",    "healthy": true,  "detail": null, "latency_ms": 1.4 }
  ]
}
```

The breakdown is the point — "not ready" tells you to look, this tells you where.

Each check runs under an explicit timeout. An early version had none, and a
probe against a stopped database hung for over twenty seconds, which is a
readiness probe that has itself become the outage.

---

## Authentication

### `POST /api/v1/auth/register` → 201

```json
{ "email": "you@example.com", "password": "at least 12 characters" }
```

Returns the created user. Passwords are hashed with **Argon2id** — memory-hard,
so a GPU gives an attacker much less advantage than it does against bcrypt or
PBKDF2 — and the hash is never returned by any endpoint.

**409** if the email is taken. This does leak that an account exists; the
alternative (pretending to succeed) makes the signup flow dishonest and the
information is recoverable from the login flow anyway.

### `POST /api/v1/auth/login` → 200

```json
{ "access_token": "eyJ...", "token_type": "bearer", "expires_in": 3600 }
```

**401** for both an unknown email and a wrong password, with the same message and
after the same work. A faster answer for an unknown email is a way to enumerate
accounts by timing.

**403** if the account is inactive.

### `GET /api/v1/auth/me` → 200 *(auth)*

The signed-in user. The cheapest way for a client to check whether its token is
still valid.

---

## GitHub linking

Links a DevPilot account to a GitHub identity. Repository *access* comes from the
App installation, not from these — OAuth here answers "who are you", not "what
may you read".

| Endpoint | Notes |
|---|---|
| `GET /api/v1/github/status` *(auth)* | Whether an identity is linked, and which |
| `GET /api/v1/github/authorize` *(auth)* | **200** `{"authorize_url"}` — the frontend fetches this with the token, then navigates |
| `GET /api/v1/github/callback` | **307** back to the frontend; **400** on a bad `state` |
| `DELETE /api/v1/github/link` *(auth)* | Unlink |

The `state` parameter is signed and time-limited, and verified on the way back.
Without that check the callback is an open redirect and a CSRF vector: anyone
could complete a link against a victim's session.

`/github/callback` is unauthenticated because the browser arrives from GitHub
without the app's bearer token. `state` is what authenticates it instead.

---

## Repositories

### `GET /api/v1/repositories` → 200 *(auth)*

Every repository this user tracks.

### `GET /api/v1/repositories/installations` → 200 *(auth)*

GitHub App installations **owned by the caller's linked GitHub identity**. The
user picks one to sync from. An account with no linked identity gets an empty
list — there is nothing to match it against.

GitHub's own endpoint returns every installation of the App; filtering here is
what stops one user seeing another's.

### `POST /api/v1/repositories/sync` → 200 *(auth)*

```json
{ "installation_id": 12345678 }
```

Reconciles the local `repositories` rows with what the installation can see:
inserts new ones, updates renames, deactivates ones that disappeared. Returns
counts of each.

The installation must belong to **your** linked GitHub identity — an id you do
not own answers `404`, exactly like one that does not exist. Without that check
the endpoint is an authorization hole: DevPilot authenticates to GitHub as the
*App*, so it would happily mint a token for any installation id it was handed
and list a stranger's private repositories into your account. See tradeoff 107.
A user who has not linked GitHub owns no installations and can sync nothing.

Rows are keyed on **(owner_id, github_repo_id)**. Two users tracking the same
repository each get their own row — under the original global constraint the
second user's sync silently inserted nothing and their dashboard was empty. See
tradeoff 72.

### `GET /api/v1/repositories/{id}` → 200 *(auth)*

One repository. **404** if it is not this user's.

### `GET /api/v1/repositories/{id}/pull-requests` → 200 *(auth)*

Pull requests DevPilot knows about in that repository.

### `POST /api/v1/repositories/{id}/index` → 202 *(auth)*

Queues an embedding pass over the repository, building the `code_chunks` that
retrieval searches.

**202, not 200.** Indexing a repository takes minutes and costs embedding calls;
holding an HTTP connection open for it would time out at every proxy in the path.
The response says the work was accepted, and the repository's `indexed_at` says
when it finished.

Re-indexing is cheap: chunks are deduplicated by content hash, so unchanged files
produce no embedding calls at all.

---

## Reviews and findings

### `GET /api/v1/reviews` → 200 *(auth)*

The dashboard feed, newest first.

| Query | Default | Notes |
|---|---|---|
| `limit` | 20 | 1–100, enforced by the schema |
| `repository_id` | — | Filter to one repository |

`limit` is bounded in the **schema**, not in the handler. An unbounded `limit` is
a denial-of-service primitive anyone can trigger by typing a large number, and a
validated bound rejects it before any SQL runs.

### `GET /api/v1/reviews/{id}` → 200 *(auth)*

One review with its findings, ordered worst-first (critical → low, then by file
and line).

Findings are loaded with `selectinload` in the same round trip. Lazy loading here
would be an N+1 that appears only at serialisation time — invisible in the code,
obvious in the query log.

Includes `prompt_tokens` and `completion_tokens`, so the cost of a review is
visible rather than inferred.

**`risk_score` is 0–100 and computed by DevPilot, never by the model.** It is a
weighted sum of finding severities (critical 10, high 7, medium 4, low 1) scaled
by 5, with a floor per highest severity present, clamped to 0–100. A score the
model chose would not be comparable between two reviews, could not be explained
to the person reading it, and would drift the moment the prompt or the model
changed. This one can be recomputed from the findings by hand — see
[`app/services/risk.py`](../backend/app/services/risk.py) and tradeoff 21.

### `GET /api/v1/pull-requests/{id}` → 200 *(auth)*

One pull request with **every** review job and every review — including failed
jobs, with their error type and message.

Exposing failures is the point. A reviewer that shows only its successes is one
you cannot debug and should not trust.

### `POST /api/v1/pull-requests/{id}/retry` → 202 *(auth)*

Queues a fresh review attempt for the same commit. **Only when the latest
attempt failed** — otherwise `409` with a sentence saying why: an attempt is
already in progress, the latest one succeeded, or it was superseded by a newer
commit. `404` for someone else's pull request.

A review can fail for reasons unrelated to the code — the model provider was
unavailable, say — and without this the only way to try again was to push a
commit. The failed attempt is kept; a new row is created.

### `POST /api/v1/reviews/{id}/publish` → 200 *(auth)*

Posts (or re-posts) a stored review's findings to its pull request. Returns what
happened:

```json
{ "posted": true, "comment_count": 1, "github_review_id": 5179187539,
  "html_url": "https://github.com/.../pull/3#pullrequestreview-...",
  "skipped_reason": null }
```

The post is the last step of the pipeline and can fail on its own — the App
lacking write access, for instance — without failing the review. This is how a
stranded review gets sent once the cause is fixed. **Idempotent:** a review
already on GitHub is left alone and `posted` comes back `false` with a
`skipped_reason`.

### `GET /api/v1/findings` → 200 *(auth)*

Findings across every review, newest first. `limit` (default 50, max 200) and
`severity`.

Findings carry `is_posted`: everything is stored and shown here, but only
findings above `DEVPILOT_LLM_MIN_CONFIDENCE_TO_POST` are posted to GitHub. A weak
finding costs nothing in a list the user opened deliberately, and a lot on a
public pull request. See tradeoff 69.

---

## Webhooks

### `POST /api/v1/webhooks/github`

The only endpoint GitHub calls. Unauthenticated in the bearer-token sense;
authenticated by **HMAC-SHA256 signature**.

Required headers:

| Header | Purpose |
|---|---|
| `X-Hub-Signature-256` | `sha256=<hex>` over the raw body |
| `X-GitHub-Event` | Event type |
| `X-GitHub-Delivery` | Unique delivery id — the idempotency key |

| Status | Meaning |
|---|---|
| `202` | Accepted; a review job was queued |
| `200` | Already handled, or an event type we ignore |
| `400` | Not valid JSON, too large, or missing required headers |
| `401` | Missing or invalid signature |

Four properties matter here, and each exists because the alternative fails:

**The signature is verified over raw bytes, before parsing.** Re-serialising JSON
changes whitespace and key order, and the resulting digest will not match. It is
also verified *first*: parsing unverified input is doing work for an attacker.
Comparison uses `hmac.compare_digest`, so a wrong signature does not leak how
much of it was right through timing. The legacy SHA-1 header is refused outright.

**A missing secret rejects everything.** Not "skip verification when
unconfigured" — that turns a forgotten variable into an open endpoint that
creates jobs.

**Delivery ids make it idempotent.** GitHub redelivers anything it thinks failed.
The id is inserted against a unique index and an `IntegrityError` means "already
seen"; a `SELECT` first would race with a concurrent redelivery of the same event.

**It answers immediately.** Verify, record, queue, return. The review itself runs
on a Celery worker, because a webhook that blocks is a webhook GitHub times out
and sends again — and the retry does the same slow work.

Unknown event types are recorded and acknowledged with 200. Returning an error
for an event we simply do not handle would make GitHub retry it forever.

---

## Not implemented

Honest omissions rather than accidental ones:

- **No pagination cursors.** `limit` only. Cursors matter past a few thousand
  rows per user, which no install has.
- **No refresh tokens.** One short-lived access token; re-login when it expires.
- **No `PATCH`/`DELETE` on reviews.** Reviews are a record of what happened.
- **No public API keys.** Bearer tokens are for the frontend, not third parties.
- **Only four webhook event types are acted on** — `ping`, `installation`,
  `installation_repositories` and `pull_request`. Everything else is recorded and
  acknowledged, never dropped silently.
