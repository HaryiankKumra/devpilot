# Deployment

How to run DevPilot somewhere other than a laptop, and what to check before you
do.

This describes a **single-host Docker Compose deployment** with TLS terminated
inside the stack. That is the right size for this project: one machine, one
`docker compose up`, and no orchestrator to learn. The section at the end says
what would have to change to run it larger.

There is a [step-by-step walkthrough for Oracle Cloud's Always Free
tier](#walkthrough-oracle-cloud-always-free) further down: a real VM that costs
nothing, which is where this project actually runs.

---

## What runs

Seven containers, defined in [`docker-compose.prod.yml`](../docker-compose.prod.yml):

| Service | Image | Role |
|---|---|---|
| `caddy` | `caddy:2-alpine` | **The only published ports.** TLS, automatic Let's Encrypt, routing |
| `postgres` | `pgvector/pgvector:pg16` | Database and vector store |
| `redis` | `redis:7-alpine` | Celery broker, result backend, rate-limit counters |
| `migrate` | `devpilot-api` | One-shot `alembic upgrade head`, must exit 0 |
| `api` | `devpilot-api` | Uvicorn, 4 workers |
| `worker` | `devpilot-api` | Celery, concurrency 4 |
| `frontend` | `devpilot-frontend` | nginx serving the built bundle |

Caddy serves the API (`/api/*`, `/health*`) and the frontend from **one origin**,
so the browser calls the host it loaded from: no CORS preflight on any request,
and one origin for cookies, CSP and rate limiting to reason about. It obtains the
certificate itself on the first HTTPS request and renews it. There is no certbot
and no cron job to forget.

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

### 4. A domain

Caddy needs a hostname to get a certificate for. If you have none, **DuckDNS**
gives you `something.duckdns.org` for free, pointed at your IP, in under a
minute. It is on the Public Suffix List, so it gets its own Let's Encrypt
rate-limit bucket rather than sharing one with every other user.

Set it in `.env.prod`:

```
DEVPILOT_DOMAIN=devpilot-yourname.duckdns.org
```

Every other URL setting follows from it:

```
DEVPILOT_CORS_ORIGINS=https://devpilot-yourname.duckdns.org
DEVPILOT_GITHUB_OAUTH_REDIRECT_URI=https://devpilot-yourname.duckdns.org/api/v1/github/callback
DEVPILOT_FRONTEND_BASE_URL=https://devpilot-yourname.duckdns.org
```

`VITE_API_BASE_URL` is derived from `DEVPILOT_DOMAIN` at build time and is not
set separately. It is a **build argument**, not a runtime variable: Vite inlines
it into the bundle, so changing the domain means rebuilding the frontend image.
That is the price of shipping static files with no runtime.

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
curl -fsS https://$DEVPILOT_DOMAIN/health          # liveness: the process is up
curl -fsS https://$DEVPILOT_DOMAIN/health/ready    # readiness: Postgres and Redis too
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=50 worker
docker compose -f docker-compose.prod.yml logs caddy | grep -i certificate
```

The first HTTPS request takes a few seconds longer than the rest: that is Caddy
obtaining the certificate. If it keeps failing, the two causes in order of
likelihood are the domain not resolving to this host yet, and port 80 blocked.
Let's Encrypt validates over plain HTTP before issuing.

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
- [ ] `DEVPILOT_DOMAIN` resolves to this host; ports 80 and 443 open in **both** firewalls
- [ ] Caddy obtained a certificate (`logs caddy | grep certificate`)
- [ ] `/health/ready` returns 200
- [ ] A test webhook produces a completed review
- [ ] `pg_dump` backup taken and restored once
- [ ] Spend limit set if `LLM_MODE=anthropic`

---

## Walkthrough: Oracle Cloud Always Free

A real virtual machine with a public IP, free indefinitely: 2 OCPUs and 12 GB on
an ARM instance, well above what this stack needs. A card is required at sign-up
for identity verification; Always Free resources are never billed.

**1. Create the instance** (console: Compute, Instances, Create)

- Image **Ubuntu 24.04** (Canonical); shape **VM.Standard.A1.Flex**, 2 OCPU / 12 GB
- Networking: create a new VCN, and tick **assign a public IPv4 address**
- SSH: paste your public key, or download the private key it generates

**2. Open the ports in the cloud firewall.** There are two firewalls and both
must be open; this is the one people miss.

Networking, Virtual cloud networks, your VCN, Security lists, Default, **Add
ingress rules**: source `0.0.0.0/0`, protocol TCP, destination port `80`; and
again for `443`.

**3. Point a domain at it.** At [duckdns.org](https://www.duckdns.org), sign in,
add a subdomain, paste the instance's public IP.

**4. Bootstrap the host.** SSH in as `ubuntu` and run:

```bash
curl -fsSL https://raw.githubusercontent.com/HaryiankKumra/devpilot/main/deploy/bootstrap.sh | bash
```

It installs Docker, opens 80/443 in the *host* firewall (the second firewall:
Oracle's images drop everything but SSH by default, in front of Docker's own
rules), clones the repository, and writes a `.env.prod` with a generated secret
key and database password. Then it stops and tells you what is left. Log out and
back in so `docker` works without `sudo`.

**5. Fill in `.env.prod`**: the domain, the Gemini keys, and the GitHub App
values, exactly as the script's closing message lists them. Copy the App's
`.pem` to `~/devpilot/secrets/github-app.pem`.

**6. Start it.**

```bash
cd ~/devpilot
docker compose -f docker-compose.prod.yml up -d --build
```

The first build takes a few minutes on the ARM instance. Then:

```bash
curl -fsS https://your.duckdns.org/health/ready
```

**7. Point the GitHub App at it.** In the App's settings, set the webhook URL to
`https://your.duckdns.org/api/v1/webhooks/github` and the callback URL to
`https://your.duckdns.org/api/v1/github/callback`. Open a pull request on an
installed repository and watch it get reviewed.

**Updating** is `git pull` and the same `up -d --build`. **Backups** are the
`pg_dump` command above; `scp` the file off the instance.

---

## Alternative: one container, no card (Render + Neon)

If you cannot get a VM -- no card, or a provider that will not take yours --
there is a second target that costs nothing and asks for no card anywhere:

| Piece | Where | Why |
|---|---|---|
| API + worker + frontend | Render free web service | 750 hours a month is exactly one service, 24/7 |
| PostgreSQL + pgvector | [Neon](https://neon.tech) free | Render's free Postgres is deleted after 30 days; Neon's is permanent |
| Redis | Render Key Value free | Broker and rate-limit counters |
| HTTPS | `https://<name>.onrender.com` | Provided; webhooks work |

The trade is that one free service means **one container**, so
the root `Dockerfile` runs the API, the Celery worker and the built
frontend together. The API serves the bundle itself (see `app/api/spa.py`),
and the entrypoint exits if *either* process dies so a dead worker cannot hide
behind a healthy API. [`render.yaml`](../render.yaml) describes both services;
Render reads it as a Blueprint and prompts for the secrets.

Two free-tier behaviours to know:

- **Spin-down.** Idle services sleep after 15 minutes and take about a minute
  to wake. A webhook arriving in that minute is lost -- GitHub does not retry.
  Point a free pinger (cron-job.org, UptimeRobot) at `/health` every 10 minutes
  and it never sleeps; 750 hours covers the whole month.
- **512 MB of RAM.** The entrypoint runs one uvicorn worker and a solo-pool
  Celery worker for that reason. It is enough for reviews; it is not enough for
  indexing a large repository.

Neon's connection string is plain `postgresql://`; paste it as-is, the
application rewrites it to the installed driver.

The Compose deployment above remains the reference. This one is what you run
when a VM is not an option.

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
