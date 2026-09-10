# Deployment

How to run DevPilot somewhere other than a laptop, and what to check before you
do.

This describes a **single-host Docker Compose deployment** behind a reverse proxy
that terminates TLS. That is the right size for this project: one machine, one
`docker compose up`, and no orchestrator to learn. The section at the end says
what would have to change to run it larger.

---

## What runs

Six containers, defined in [`docker-compose.prod.yml`](../docker-compose.prod.yml):

| Service | Image | Role |
|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | Database and vector store |
| `redis` | `redis:7-alpine` | Celery broker, result backend, rate-limit counters |
| `migrate` | `devpilot-api` | One-shot `alembic upgrade head`, must exit 0 |
| `api` | `devpilot-api` | Uvicorn, 4 workers |
| `worker` | `devpilot-api` | Celery, concurrency 4 |
| `frontend` | `devpilot-frontend` | nginx serving the built bundle |

The API, the worker and the migration job are the **same image with the same
environment**, sourced from one YAML anchor. A worker pointed at a different
database than the API is a bug that takes hours to find and seconds to prevent.

`docker-compose.prod.yml` is a separate file rather than an override layered onto
`docker-compose.yml`. An override can add and replace keys but cannot remove
them, so the development bind mounts and `--reload` would survive into
production — and not shipping those is the entire point of the file.

---

## Before the first deploy

### 1. A machine

2 vCPU and 4 GB RAM is enough for a single-team install. The worker is the
memory-hungry service (diffs and prompts are held in memory); Postgres wants the
disk.

Docker Engine 24+ and the Compose plugin v2.

### 2. Real secrets

```bash
cp .env.example .env.prod
```

Then change every one of these. The application **refuses to start in production**
while `DEVPILOT_SECRET_KEY` is still the development placeholder — that guard
exists because a leaked signing key means anyone can mint a token for any
account, and a placeholder secret is a leaked key by definition.

| Variable | What to set |
|---|---|
| `DEVPILOT_SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `POSTGRES_PASSWORD` | A generated password, not `devpilot` |
| `DEVPILOT_GITHUB_APP_ID` | From the App's settings page |
| `DEVPILOT_GITHUB_APP_PRIVATE_KEY_PATH` | Path to the mounted `.pem` |
| `DEVPILOT_GITHUB_WEBHOOK_SECRET` | The secret registered on the App |
| `DEVPILOT_GITHUB_CLIENT_ID` / `_SECRET` | OAuth credentials |
| `DEVPILOT_GEMINI_API_KEYS` | Only if `DEVPILOT_LLM_MODE=gemini` (free tier) |
| `DEVPILOT_ANTHROPIC_API_KEY` | Only if `DEVPILOT_LLM_MODE=anthropic` (paid) |
| `DEVPILOT_VOYAGE_API_KEY` | Only if `DEVPILOT_EMBEDDING_MODE=live` |

`.env.prod` is git-ignored by the `.env.*` rule. Keep it out of the image too —
it is read by Compose at run time, never `COPY`d.

The GitHub private key is best mounted as a file rather than pasted inline:

```yaml
    volumes:
      - /etc/devpilot/github-app.pem:/run/secrets/github-app.pem:ro
```

and `DEVPILOT_GITHUB_APP_PRIVATE_KEY_PATH=/run/secrets/github-app.pem`. A file
does not appear in `docker inspect`, in `ps`, or in a crash dump of the
environment.

### 3. Production-only settings

These differ from local development and `docker-compose.prod.yml` sets them for
you — the point of listing them is that you should know they changed:

```
DEVPILOT_ENVIRONMENT=production      # JSON logs, no /docs, no error detail
DEVPILOT_DEBUG=false
DEVPILOT_TRUST_PROXY_HEADERS=true    # only correct behind a proxy (see below)
DEVPILOT_ENABLE_HSTS=true            # only correct behind real TLS
```

Still to set yourself in `.env.prod`:

```
DEVPILOT_CORS_ORIGINS=https://devpilot.example.com
DEVPILOT_GITHUB_OAUTH_REDIRECT_URI=https://devpilot.example.com/api/v1/github/callback
DEVPILOT_FRONTEND_BASE_URL=https://devpilot.example.com
VITE_API_BASE_URL=https://devpilot.example.com
```

`VITE_API_BASE_URL` is a **build argument**, not a runtime variable: Vite inlines
it into the bundle. Changing it requires rebuilding the frontend image. That is
the price of shipping static files with no runtime, and it is worth paying.

`DEVPILOT_TRUST_PROXY_HEADERS=true` is only correct if something in front
actually overwrites `X-Forwarded-For`. The header is client-supplied; trusting it
without a proxy lets any caller reset its own rate limit by inventing an address.

### 4. TLS

Compose binds the API to `127.0.0.1:8000` and the frontend to `127.0.0.1:8080`,
so neither is reachable from the internet directly. Put a proxy in front. Caddy
is the least ceremony:

```
devpilot.example.com {
    handle /api/* {
        reverse_proxy 127.0.0.1:8000
    }
    handle /health* {
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        reverse_proxy 127.0.0.1:8080
    }
}
```

Serving the frontend and the API from one origin removes the CORS preflight from
every request and means cookies and CSP have one origin to reason about.

---

## Deploying

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Compose orders this correctly on its own: `postgres` and `redis` must report
healthy, then `migrate` runs and must exit 0, and only then do `api` and `worker`
start (`condition: service_completed_successfully`).

Migrations run as a **one-shot job, not on API startup**. With four uvicorn
workers, startup migrations would mean four processes running
`alembic upgrade head` against one database simultaneously; Alembic takes a lock,
so three of them block on boot and a slow migration becomes a failed deploy. A
job runs once and fails the deploy loudly if the schema cannot be brought up to
date — which is the outcome you want, because serving traffic against a schema
the code does not expect is worse than not serving traffic.

### Verifying

```bash
curl -fsS https://devpilot.example.com/health          # liveness: the process is up
curl -fsS https://devpilot.example.com/health/ready    # readiness: Postgres and Redis too
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=50 worker
```

`/health/ready` returning 503 with a per-dependency breakdown is the fastest way
to find out which backing service is the problem.

Then send a real webhook: open a pull request on an installed repository and
watch a `review_jobs` row go `queued -> running -> completed`.

### Updating

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

There is a **brief outage** while containers restart. Removing it needs two API
replicas and a proxy that drains connections, which is the first thing to add if
this ever matters. What the current setup does guarantee is that migrations
complete before new code serves traffic.

Migrations must be **backward compatible with the running code** during that
window: add columns before writing to them, and drop columns in a later release
than the one that stopped reading them. Every migration here has a working
`downgrade()`, so a bad deploy can be reversed:

```bash
docker compose -f docker-compose.prod.yml run --rm migrate alembic downgrade -1
```

---

## Operating it

### Logs

Structured JSON in production, one object per line, with a request id on every
line for a given request. `docker compose logs` is fine for one host; ship them
to something searchable when there is more than one.

Every container caps its log file at 10 MB × 5 files. Without that cap, one
chatty container fills the host disk and takes every other service down with it.

### Backups

Only `postgres-data` matters. `redis-data` holds queued jobs — losing it loses
in-flight reviews, which GitHub will redeliver anyway.

```bash
docker compose -f docker-compose.prod.yml exec -T postgres \
    pg_dump -U devpilot devpilot | gzip > devpilot-$(date +%F).sql.gz
```

Restore into a scratch database and query it. A backup nobody has restored is a
hypothesis, not a backup.

### What to watch

| Signal | Where | Why it matters |
|---|---|---|
| `/health/ready` non-200 | HTTP probe | A dependency is down |
| `review_jobs` stuck `queued` | SQL | The worker is dead or the broker is unreachable |
| `review_jobs.status = 'failed'` rate | SQL | Usually GitHub or the LLM provider |
| `rate_limit.unavailable` | Logs | Redis is failing and the limiter is failing **open** |
| Celery queue depth | `redis-cli llen celery` | Reviews arriving faster than they finish |

### Scaling

```bash
docker compose -f docker-compose.prod.yml up -d --scale worker=3
```

Workers are stateless and coordinate through Redis, so this is safe. Job claiming
is a conditional `UPDATE ... WHERE status = 'queued'`, so two workers handed the
same job cannot both process it.

Scaling the API needs a load balancer in front, since a published host port
cannot be shared. Watch connection count when you do: each uvicorn worker opens
its own pool, so `replicas × workers × DEVPILOT_DB_POOL_SIZE` must stay under
Postgres `max_connections` (100 by default). At the shipped values that is
1 × 4 × 5 = 20, plus overflow.

### Cost control

`DEVPILOT_LLM_MODE=anthropic` bills per review; `gemini` runs on a free tier.
Before enabling the paid provider:

- set a spend limit in the Anthropic console — it is the only hard stop;
- consider `DEVPILOT_LLM_EFFORT=medium`, which is usually enough for routine
  changes and costs meaningfully less;
- leave `DEVPILOT_MAX_DIFF_BYTES` alone. Enormous diffs produce prompts no model
  reads carefully, so refusing them saves money *and* improves output.

`mock` mode runs the whole pipeline for free and is what CI and the end-to-end
tests use.

---

## Deployment checklist

- [ ] `DEVPILOT_SECRET_KEY` generated, not the placeholder
- [ ] `POSTGRES_PASSWORD` generated
- [ ] `DEVPILOT_ENVIRONMENT=production`, `DEVPILOT_DEBUG=false`
- [ ] GitHub App private key mounted as a file, not inline
- [ ] Webhook URL on the App points at `https://.../api/v1/webhooks/github`
- [ ] OAuth callback URL matches `DEVPILOT_GITHUB_OAUTH_REDIRECT_URI` exactly
- [ ] `DEVPILOT_CORS_ORIGINS` lists only the real frontend origin
- [ ] TLS terminating in front; `DEVPILOT_ENABLE_HSTS=true`
- [ ] `DEVPILOT_TRUST_PROXY_HEADERS=true` **and** a proxy that sets the header
- [ ] `/health/ready` returns 200
- [ ] A test webhook produces a completed review
- [ ] `pg_dump` backup taken and restored once
- [ ] Spend limit set if `LLM_MODE=anthropic`

---

## What this deliberately is not

**No Kubernetes.** One host running Compose is understandable end to end and
recoverable by one person at 2am. Kubernetes buys rolling deploys, autoscaling
and self-healing across nodes — real benefits that this workload does not yet
need and that cost a control plane, a manifest tree, and an ingress controller to
learn. The migration path is straightforward if it ever does: the images are
already stateless and configured entirely through environment variables, which is
the part that is actually hard to retrofit.

**No message broker beyond Redis.** Kafka would give durable, replayable,
partitioned streams. The queue here holds a few review jobs, and GitHub already
redelivers anything lost.

**No service mesh, no autoscaler, no separate metrics stack.** Each would be
infrastructure added ahead of the problem it solves.

**Revisit when:** reviews outpace a single host's workers, or the deploy outage
stops being acceptable — whichever comes first.
