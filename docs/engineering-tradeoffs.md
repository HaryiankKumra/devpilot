# Engineering tradeoffs

Decisions that could reasonably have gone the other way, what was chosen, and
what it costs. Each entry is written to answer the interview follow-up: *"what
would you have done differently, and when would that be the better choice?"*

Decisions are recorded as they are made, so this file grows with the project.

---

## 1. Modular monolith with a worker, not microservices

**Chosen:** two processes (API and Celery worker) sharing one codebase and one
database.

**Alternative:** separate services for ingestion, indexing, LLM calls and the
API.

**Why:** the only boundary that relieves real pressure here is latency — HTTP
requests must be fast, reviews are slow. That is exactly one split, and the
worker provides it. Every further split would add a network hop and a deployment
unit without decoupling anything, because all of those components read and write
the same rows. Microservices trade local function calls for distributed
transactions; that trade is worth making when teams need independent deploy
cadences, which is not a problem a single-maintainer project has.

**Cost:** the API and the worker scale together in the sense that they share a
schema, so a migration must be compatible with both during a rolling deploy.
**Revisit when:** indexing volume needs hardware the API does not (GPU
embedding, for instance), or separate teams own separate parts.

## 2. Synchronous SQLAlchemy in an async framework

**Chosen:** synchronous SQLAlchemy 2.0 everywhere; FastAPI endpoints declared
`def` rather than `async def`.

**Alternative:** async SQLAlchemy with `asyncpg` in the API.

**Why:** Celery workers are synchronous, and both the API and the worker use the
same data-access code. Async in the API would force either two parallel sets of
repositories or an event loop inside Celery tasks. One code path is simpler to
read, simpler to test and simpler to explain. FastAPI runs `def` endpoints in a
thread pool, so blocking calls do not stall the event loop.

**Cost:** each in-flight request occupies a thread, so raw connection-holding
concurrency is lower than an async stack would give. That is acceptable because
the API is I/O-light by design — the expensive work is on the worker.

**Revisit when:** the API itself becomes the bottleneck under load testing
(Milestone 11 will measure this). The first fix is more worker processes, not an
async rewrite.

## 3. Deterministic risk scoring instead of asking the LLM

**Chosen:** the LLM returns findings with severities; the 0–100 risk score is
computed in Python from weighted severities.

**Alternative:** let the model return `risk_score` directly.

**Why:** the same PR must produce the same score. A model asked for a number
gives a plausible one that varies between runs and cannot be justified to a user
who asks why their PR scored 72. Weighted severities (`critical=10`, `high=7`,
`medium=4`, `low=1`) normalised to 0–100 are deterministic, unit-testable, and
tunable without touching a prompt.

**Cost:** the weights are a judgement call, and a linear sum treats ten low
findings as comparable to one critical one. Normalisation has to be chosen
carefully so a large PR is not penalised purely for size.

**Revisit when:** real usage shows the ranking disagrees with reviewer intuition;
the weights are configuration, not architecture.

## 4. Validating LLM output rather than trusting it

**Chosen:** every LLM response is parsed into a Pydantic model. Non-conforming
output is rejected and retried, never persisted.

**Alternative:** parse the JSON loosely and store what comes back.

**Why:** an LLM response is untrusted input that happens to be expensive. It can
return prose around the JSON, invent enum values, or cite a file the diff never
touched. Storing unvalidated output means those defects surface later as
rendering bugs or, worse, as comments posted to a customer's pull request.

**Cost:** stricter validation means more retries, and a retry costs another API
call. Some genuinely useful-but-malformed responses get discarded.

**Revisit when:** never for the validation itself; the retry budget is a tuning
question for Milestone 7.

## 5. Signature verification over raw bytes, before parsing

**Chosen:** read the raw request body, verify the HMAC against those exact
bytes, then parse.

**Alternative:** parse the JSON body with FastAPI's normal model binding, then
verify.

**Why:** the signature covers the bytes GitHub sent. Parsing and re-serialising
produces *different* bytes — key order, whitespace and unicode escaping all
shift — so verification against re-serialised JSON either fails constantly or,
if made lenient enough to pass, verifies nothing. This is a correctness
requirement, not a preference.

**Cost:** the endpoint gives up FastAPI's automatic request-model binding and
does its own parsing, so it is more verbose than a typical route.

## 6. A dedicated table for webhook idempotency

**Chosen:** a `webhook_events` table with a unique constraint on the GitHub
delivery id.

**Alternative:** a Redis `SETNX` on the delivery id.

**Why:** Redis is configured for durability here, but it is still a cache in
role, and the consequence of losing a key is a duplicate review posted to a
customer's PR. A unique constraint makes the guarantee the database's problem,
and it holds under concurrent retries: the second insert simply fails. The table
also doubles as an audit log of what GitHub actually sent, which is invaluable
when debugging a delivery that behaved unexpectedly.

**Cost:** a database write on every webhook, and a table that grows
monotonically and will eventually need a retention policy.

## 7. Vite 6 rather than the latest scaffold

**Chosen:** Vite 6 with the esbuild/Rollup toolchain, TypeScript 5.6, Tailwind 3
with PostCSS.

**Alternative:** the current `create-vite` default — Vite 8 with Rolldown,
TypeScript 6, Tailwind 4.

**Why:** this was decided empirically, not by preference. The Vite 8 scaffold
installed but its Rolldown native binding failed to load on the development
machine's Node 20.12, and `npm run build` crashed. Tailwind 4 carries the same
class of risk through its Lightning CSS native binding. The pinned stack is pure
JavaScript apart from esbuild, builds reproducibly, and is the version most
documentation and Stack Overflow answers currently describe.

**Cost:** the project is a major version behind on its build tooling and will
need a deliberate upgrade later.

**Revisit when:** the toolchain's Node floor is comfortably met and the native
bindings install cleanly — verify with an actual build, not a successful
`npm install`.

## 8. No Makefile

**Chosen:** document the exact commands in the README.

**Alternative:** a Makefile with `make test`, `make lint`, `make up`.

**Why:** `make` is not available on the development machine (Windows), so a
Makefile would be a set of commands the project's own maintainer cannot run —
which is worse than no abstraction at all, because it looks authoritative while
being broken. Explicit commands work on every platform and are copy-pasteable
into CI.

**Cost:** commands are longer to type and are repeated between the README and
the CI workflow, so they can drift.

**Revisit when:** CI is added in Milestone 12 — if the two copies start
disagreeing, a task runner that works on Windows (`just`, or npm scripts) earns
its place.

## 9. Placeholder pages that admit they are placeholders

**Chosen:** unbuilt routes render an explicit "not implemented yet" panel naming
the milestone that will deliver them.

**Alternative:** mock data, or omitting the routes entirely.

**Why:** mock data in a portfolio project is indistinguishable from a bug, and it
invites the reviewer to believe a feature works when it does not. Omitting the
routes would hide the intended structure. Naming the milestone turns the gap into
visible roadmap.

**Cost:** the running application looks unfinished — which is accurate.

## 10. Explicit timeouts on every connection pool

**Chosen:** `connect_timeout`, `statement_timeout`, `pool_timeout` and
`pool_pre_ping` are all set explicitly on the database engine, with matching
socket timeouts on Redis.

**Alternative:** the library defaults.

**Why:** discovered by testing rather than by reasoning. With default settings,
pointing the readiness probe at a database that was not running did not produce a
`503` — the request *hung* for over twenty seconds, waiting out the operating
system's TCP retry budget. A readiness probe that hangs is strictly worse than
one that fails, because the orchestrator learns nothing and the client's own
timeout decides the outcome. The pool configuration is asserted in
`tests/test_db_session.py` so the fix cannot silently regress.

**Cost:** a genuinely slow query can now be killed by `statement_timeout`, so
that limit has to be sized against real workloads rather than left generous.

## 11. Health probes split into liveness and readiness

**Chosen:** `/health` touches nothing; `/health/ready` probes Postgres and Redis.

**Alternative:** one endpoint that checks everything.

**Why:** the two answers drive opposite actions. Failing liveness means *restart
this container*; failing readiness means *stop sending it traffic*. A single
endpoint that checks dependencies makes a brief database blip restart every API
container at once — turning a recoverable dependency outage into a full outage
with cold starts on top.

**Cost:** two endpoints to document, and the deployment must be configured to
point each probe at the right one; wiring liveness to the readiness path
reintroduces the exact problem.

## 12. Sequential rather than concurrent readiness probes

**Chosen:** probe Postgres, then Redis, one after the other.

**Alternative:** probe both concurrently in a thread pool.

**Why:** worst-case response time is the sum of the connect timeouts. Measured
against stopped services, that is ~5s under Docker Compose, where `postgres` and
`redis` each resolve to a single address. Running the API directly against
`localhost` roughly doubles it (~10s observed), because `localhost` resolves to
both `::1` and `127.0.0.1` and the driver tries each in turn. Both fit inside a
typical ten-second probe timeout for the deployment that matters — Compose — and
running a thread pool inside a request handler to save a few seconds on a path
that only executes when the system is already broken is complexity without a
payoff.

**Cost:** adding a third dependency pushes the worst case up additively, so this
holds only while the number of dependencies stays small.

**Revisit when:** a fourth backing service appears, or the probe timeout budget
tightens.
