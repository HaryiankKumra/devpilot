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

---

# Milestone 2 decisions

## 13. Access tokens only, with no refresh token

**Chosen:** a single short-lived (60 minute) signed access token. No refresh
token, no server-side session.

**Alternative:** a short access token plus a long-lived refresh token, stored
hashed in the database and rotated on use.

**Why:** the refresh-token design is better, and it is what this should become.
It is deferred because it needs a table, rotation logic, reuse detection and a
revocation path, and none of that is useful until there is something to protect
beyond a login form. The 60-minute lifetime is the mitigation: a stolen token
stops working on its own.

**Cost:** the real one is that **a token cannot be revoked before it expires**.
Deactivating an account is handled -- the user row is re-read on every request,
so a disabled account stops working immediately -- but a leaked token for an
account that is still active remains valid until it expires.

**Revisit when:** Milestone 11, alongside the rest of the security hardening.

## 14. Argon2id, used directly, instead of bcrypt via passlib

**Chosen:** `argon2-cffi` called directly.

**Alternative:** bcrypt, usually through `passlib`.

**Why:** Argon2id is memory-hard, so an attacker with GPUs gains far less
against it than against bcrypt. bcrypt also silently truncates at 72 bytes,
which quietly discards most of a long passphrase. `passlib` is avoided because
it is no longer actively maintained, and the only thing it would add here is a
layer of indirection over two function calls.

**Cost:** Argon2 uses noticeably more memory per hash, which matters if login
throughput is ever high enough for the hashing cost to dominate.

## 15. UUID primary keys generated in Python

**Chosen:** `uuid4` primary keys, generated by the application.

**Alternative:** auto-incrementing integers, or database-generated UUIDs.

**Why:** these ids appear in URLs and API responses. A sequential integer leaks
how many rows exist and invites a client to walk its neighbours looking for
another user's data. Generating in Python rather than via a database default
means the id is known before the INSERT, so a parent and its children can be
built in one flush instead of round-tripping to read the id back.

**Cost:** UUIDs are 16 bytes rather than 4 or 8, and random ones scatter inserts
across the B-tree instead of appending, which costs some write locality.

**Revisit when:** insert volume makes index churn measurable; UUIDv7 keeps the
opacity while restoring time-ordering.

## 16. Enums as VARCHAR with a CHECK constraint, not native PostgreSQL enums

**Chosen:** `native_enum=False`, storing the enum's *value* rather than its
Python name.

**Alternative:** a real PostgreSQL `ENUM` type.

**Why:** adding a member to a native enum requires `ALTER TYPE`, which in older
PostgreSQL versions cannot run inside a transaction and is awkward to roll back.
A `CHECK` constraint gives the same guarantee and is trivial to change. Storing
values (`critical`) rather than names (`CRITICAL`) matters because names are an
implementation detail: renaming a member would invalidate every stored row,
and values are already what the API and the LLM speak.

**Cost:** slightly more storage than an enum's 4-byte OID, and the constraint is
easier to drop by accident than a type is.

## 17. SQLite for the fast tests, with a migration-drift test to cover the gap

**Chosen:** application tests run against in-memory SQLite; a separate test
compares the Alembic migrations to the ORM metadata as rendered PostgreSQL DDL.

**Alternative:** run every test against a real PostgreSQL in Docker.

**Why:** testing against the production engine is the better default, and CI
will do exactly that in Milestone 12. For the local loop, SQLite means the suite
runs in seconds with nothing installed, which is what keeps it actually being
run. The honest risk is divergence -- SQLite is not PostgreSQL -- and the
mitigation is targeted: `tests/test_migrations.py` renders the migrations to
PostgreSQL DDL offline and asserts, table by table, that they still match the
models. That catches the failure this setup would otherwise hide, where someone
edits a model, forgets the migration, and the suite stays green while production
diverges.

**Cost:** PostgreSQL-specific behaviour (JSONB operators, `ON CONFLICT`,
pgvector similarity) is not exercised locally. Those tests are marked
`integration`.

## 18. pgvector split into its own migration

**Chosen:** `0001` creates seven tables; `0002` enables the `vector` extension
and creates `code_chunks`.

**Alternative:** one initial migration containing everything.

**Why:** `CREATE EXTENSION` needs privileges an application role often lacks on
a managed database. Isolating it means such a deployment fails on a single,
obviously-named migration rather than making the initial schema look broken, and
an administrator can pre-provision the extension so the `IF NOT EXISTS` becomes
a no-op. It also lets the portable part of the schema be exercised on SQLite.

**Cost:** two migrations to apply instead of one, and a reviewer has to know why
they are separate -- which is why both files say so at the top.

## 19. Storing the access token in localStorage

**Chosen:** `localStorage`, wrapped in a module that caches the value and
tolerates storage being unavailable.

**Alternative:** memory only, or an httpOnly cookie.

**Why:** memory-only is more resistant to XSS but signs the user out on every
refresh unless a refresh token restores the session -- which does not exist yet
(decision 13). An httpOnly cookie is the strongest option and is the eventual
target, but it brings CSRF protection and cross-origin cookie configuration with
it.

**Cost:** stated plainly: any script that runs on the page can read the token.
The short expiry limits the window, and the storage is isolated behind one
module so the swap to cookies touches one file.

**Revisit when:** Milestone 11, together with refresh tokens.

## 20. Registration does not return a token

**Chosen:** `POST /auth/register` returns the created user; the client then
calls `POST /auth/login`.

**Alternative:** return a token straight from registration.

**Why:** it keeps "prove who you are" in exactly one place. A future invite flow,
or an administrator creating an account on someone's behalf, must not implicitly
hand out a session to whoever made the request. Email verification, when it
arrives, slots in between the two steps without changing the login contract.

**Cost:** one extra round trip on signup. The registration page hides it by
chaining the two calls.

---

# Milestones 3 and 4 decisions

## 21. A mock GitHub, shipped as a first-class mode

**Chosen:** `DEVPILOT_GITHUB_MODE=mock` serves the GitHub API from an in-process
fake, and it is the default.

**Alternative:** require a registered GitHub App to run anything.

**Why:** registering an App, generating a private key and exposing a public
webhook URL is a genuine barrier — for a reviewer of this project, for a new
contributor, and for CI. The mock removes it entirely. What keeps it honest is
that it returns the same Pydantic-validated types as the real client and
implements the same Protocol, so code exercised against it works against the
real API. It supplies repositories and pull requests; it never fabricates a
review.

**Cost:** a second implementation to keep in step with the first. The Protocol
makes divergence a type error rather than a runtime surprise.

## 22. A Protocol for the GitHub client, not a base class

**Chosen:** `GitHubClient` is a `typing.Protocol`; the real and mock clients
implement it independently.

**Alternative:** an abstract base class both inherit from.

**Why:** a base class invites shared behaviour, and shared behaviour between a
real client and a fake is exactly what makes a fake stop predicting reality. A
Protocol carries no implementation, so the mock cannot accidentally inherit
retry logic or header construction that the real client is supposed to own.

**Cost:** the two implementations repeat their method signatures. mypy catches
any drift.

## 23. Repository sync keyed on GitHub's id, and deactivation instead of deletion

**Chosen:** upsert on `github_repo_id`; repositories that vanish are marked
inactive.

**Alternative:** key on `full_name`, and delete rows that disappear.

**Why:** repositories get renamed and transferred between organisations. Keying
on the name would create a second row on every rename and orphan the review
history attached to the old one; GitHub's numeric id is the only identifier that
survives. Deletion is worse still: it cascades to pull requests, reviews and
findings, discarding history that is still worth reading — and an app that is
uninstalled is very often reinstalled, at which point the same rows should come
back rather than being recreated empty.

**Cost:** inactive rows accumulate and need filtering in queries. That is a much
cheaper problem than lost history.

## 24. Verifying webhook signatures over raw bytes, before parsing

**Chosen:** read `await request.body()`, verify the HMAC, and only then parse
JSON. The route does not use FastAPI's request-model binding.

**Alternative:** bind a Pydantic model as usual and verify afterwards.

**Why:** the signature covers the exact bytes GitHub sent. Parsing and
re-serialising produces different bytes — key order, whitespace and unicode
escaping all shift — so verification against re-serialised JSON either fails
constantly or has been loosened until it proves nothing. Model binding happens
before any handler code runs, so it forecloses the option entirely.

Comparison uses `hmac.compare_digest`. A plain `==` on the digest returns as
soon as two bytes differ, so its timing leaks how much of the prefix was
correct — enough to recover a valid signature byte by byte.

**Cost:** the endpoint parses its own body and is more verbose than a typical
route. That verbosity is the security property.

## 25. Refusing every webhook when no secret is configured

**Chosen:** with no `DEVPILOT_GITHUB_WEBHOOK_SECRET`, every delivery is rejected
with 401.

**Alternative:** skip verification when no secret is set, so local development
is easier.

**Why:** "verification is optional when unconfigured" means one missing
environment variable silently turns a security control off, in exactly the
deployment where nobody notices. Anyone who guessed the URL could then create
review jobs and cause comments to be posted. Local development is served by mock
mode instead, which needs no webhooks at all.

**Cost:** you cannot poke the webhook endpoint with curl without computing a
signature first. `tests/test_webhook_api.py` shows how in three lines.

## 26. Idempotency by insert-and-catch, not check-then-insert

**Chosen:** insert the delivery id inside a SAVEPOINT and treat the unique
violation as "already seen".

**Alternative:** `SELECT` for the delivery id first, and insert if absent.

**Why:** check-then-insert has a window between the two statements in which a
concurrent retry also sees "not present", and both proceed to create a review.
GitHub retries aggressively, and its retries can overlap. The unique constraint
has no such window — the database serialises the two inserts and exactly one
wins. The SAVEPOINT matters because without it the constraint violation would
poison the whole transaction, and the request could not go on to answer.

**Cost:** using an exception for expected control flow reads oddly. The
alternative is a race condition that produces duplicate comments on a customer's
pull request.

## 27. Recording ignored and failed deliveries rather than dropping them

**Chosen:** every signed delivery is written to `webhook_events`, including ones
DevPilot does not act on, with a status of `ignored` or `failed`.

**Alternative:** only store deliveries that produce work.

**Why:** the table is the idempotency ledger, so a delivery that is not recorded
is a delivery whose retry is not recognised. It also doubles as an audit trail:
"why was this pull request never reviewed?" is answerable in one query when the
ignored deliveries and their reasons are all present.

**Cost:** the table grows monotonically and will eventually need a retention
policy. Noted for Milestone 12.

## 28. Answering 2xx for duplicates and unhandled events

**Chosen:** a redelivery answers 200; an event DevPilot does not act on answers
200; only genuine work answers 202.

**Alternative:** 409 for duplicates, 400 for unhandled events.

**Why:** GitHub reads any non-2xx as a failed delivery and retries it. Answering
409 to a retry guarantees another retry, and another — the endpoint would fight
its own idempotency. The distinction the caller needs is in the body, which is
what the App's delivery log displays.

**Cost:** "success" covers several distinct outcomes at the HTTP layer. The
response body names which one.

## 29. Cancelling superseded review jobs on a new push

**Chosen:** when a push moves the head commit, unfinished jobs for the previous
commit are marked `cancelled` and a new one is queued.

**Alternative:** let every queued job run to completion.

**Why:** the old diff is no longer what anyone will merge, so reviewing it
spends an LLM call — the most expensive thing DevPilot does — producing findings
about code that has already changed. On an actively developed pull request that
is most of the cost for none of the value. The rows are kept and only their
status changes, so the audit trail survives.

**Cost:** a review that was nearly finished is discarded. Milestone 5 can refine
this to let a job that has already started run to completion.

## 30. Not reviewing draft pull requests

**Chosen:** deliveries for drafts are recorded but queue no review.

**Alternative:** review everything, and let the author ignore the comments.

**Why:** a draft is the author explicitly saying the work is not ready. Posting
review comments on it is noise, and each one costs an LLM call on code that is
still being written. `ready_for_review` is in the reviewable action set, so a
review is queued the moment the author says it is ready.

**Cost:** someone who works permanently in draft mode gets no reviews. Making
this configurable per repository is a natural later addition.

---

# Milestone 5 decisions

## 31. `acks_late`, and paying for it with a conditional claim

**Chosen:** `task_acks_late=True` and `task_reject_on_worker_lost=True`, with job
claiming done as a single conditional UPDATE.

**Alternative:** Celery's default, acknowledging a task when the worker receives
it.

**Why:** the default acknowledges on receipt, so a worker killed mid-review --
a deploy, an OOM, a reclaimed spot instance -- silently loses the job. Nothing
retries it and nothing records that it vanished. Acknowledging late means the
broker never saw an ack and redelivers, so the work survives the crash.

The cost is that a task can now be delivered twice, and two workers can hold it
at once. That is why claiming is `UPDATE ... WHERE status = 'queued'` rather
than a read followed by a write: the read-then-write version has a window in
which both workers see `queued` and both proceed, and the review gets done
twice. The database closes that window -- exactly one UPDATE reports a row.

**Cost:** every task must be safe to receive twice, which constrains how future
pipeline stages are written.

## 32. Enqueue after commit, never inside the transaction

**Chosen:** the webhook route commits the job row, and only then publishes the
Celery task.

**Alternative:** publish inside the request handler before committing, which
reads more naturally.

**Why:** Redis and PostgreSQL share no transaction. Publishing first lets a
worker pick the task up before the commit lands -- or, if the transaction rolls
back, instead of it -- and look for a row that does not exist. Committing first
inverts the failure mode: a crash in the gap leaves a `queued` row that nobody
was told about, which a periodic sweep can find later. A lost row is data loss;
a delayed job is a delay.

For the same reason a dispatch failure is logged and swallowed rather than
raised. If Redis is down when a webhook arrives, the job is already safely
recorded, and returning 500 would only make GitHub redeliver work we already
have.

**Cost:** a job can sit in `queued` unnoticed if the broker was down at exactly
the wrong moment. The sweeper that fixes this is not written yet, and is noted
for Milestone 11.

## 33. A task takes a job id, not a job

**Chosen:** `review_pull_request(review_job_id: str)`.

**Alternative:** pass the pull request details in the message.

**Why:** task arguments travel through Redis as JSON and may sit there for a
long time -- after a retry with backoff, hours. Anything embedded in the message
is a snapshot that was true when it was published. The database is the source of
truth, so the message carries only a pointer to it and the worker reads current
state. It also keeps secrets and large diffs out of the broker entirely.

**Cost:** one extra query at the start of every task.

## 34. Explicit retries instead of `autoretry_for`

**Chosen:** catch transient errors, write the job back to `queued` with the
error recorded, then call `self.retry()`.

**Alternative:** Celery's `autoretry_for=(...)`, which is one line.

**Why:** `autoretry_for` retries the *task* without touching the job row, so the
database would show a job stuck in `running` while Celery quietly tried again,
and a job that eventually exhausted its retries would have no record of why.
Doing it by hand keeps the row and the broker in step, and preserves the reason
for each failure along the way.

**Cost:** more code in the task, and the retry path has to be tested rather than
trusted.

## 35. Only transient failures are retried

**Chosen:** an explicit `TRANSIENT_ERRORS` tuple -- GitHub 5xx, rate limits,
connection errors, timeouts. Everything else fails permanently on the first
attempt.

**Alternative:** retry every exception.

**Why:** retrying a bug produces the same bug three times more slowly, and buries
the real error under two duplicates. Worse, on a paid LLM call it costs three
times as much to learn the same thing. Classifying up front means the error
record says what actually happened and how many attempts it was worth.

**Cost:** a genuinely transient failure of an unanticipated type is treated as
permanent. Adding it to the tuple is a one-line change once observed.

## 36. Honouring GitHub's `Retry-After` over our own backoff

**Chosen:** when a rate limit carries `Retry-After`, wait exactly that long;
otherwise back off exponentially from 30s, capped at 600s.

**Alternative:** always use our own backoff curve.

**Why:** GitHub knows precisely when the limit resets. Guessing shorter wastes a
request against an exhausted budget and can extend the block; guessing longer
idles a worker for no reason. Where GitHub does not say, exponential backoff with
a cap is the standard compromise -- retrying immediately makes an overloaded API
worse for everyone, and unbounded doubling eventually schedules a retry days out.

**Cost:** two code paths for one decision, which is why the choice lives in a
single tested function rather than inline in the task.

## 37. The review pipeline is a documented seam, not a stub that pretends

**Chosen:** `execute_review` raises `PipelineNotImplementedError`, and every job
currently ends `failed` with that recorded as its reason.

**Alternative:** return a placeholder review so the flow "works" end to end.

**Why:** a placeholder review is indistinguishable from a real one in the
database and on the dashboard, and it would make Milestones 6 to 9 look finished
before they exist. Failing loudly with a named error means the queue, the worker
and the failure handling are genuinely exercised, while the missing piece stays
obviously missing. The error is classified permanent so it is not retried three
times -- retrying cannot make an unwritten pipeline appear.

**Cost:** the success path never executes in production today, so it is covered
by substituting a pipeline that returns. That substitution is the only place a
test stands in for real behaviour.

## 38. The worker shares the API's image

**Chosen:** one Dockerfile; the worker service overrides the command.

**Alternative:** a separate, slimmer worker image.

**Why:** the worker imports the same models, services and settings as the API. If
the two were built separately they could drift a deploy apart and disagree about
the schema they share -- the failure mode being a worker writing columns the API
does not know about, or vice versa. One image makes that impossible and halves
the build.

**Cost:** the worker image carries uvicorn and the HTTP stack it never runs. A
few megabytes, against a class of bug that is genuinely hard to diagnose.

## 39. `worker_prefetch_multiplier=1`

**Chosen:** a worker reserves one task at a time.

**Alternative:** Celery's default of 4.

**Why:** prefetching assumes tasks are short and uniform. Reviews are neither --
one may take five seconds and the next five minutes. A worker that has reserved
four tasks holds them even while busy, so they wait behind a slow review while
another worker sits idle. With a multiplier of 1 the queue distributes to
whoever is actually free.

**Cost:** slightly more broker chatter, which is irrelevant at this task rate.

## 40. JSON serialisation only

**Chosen:** `accept_content=["json"]`.

**Why:** Celery historically defaulted to `pickle`, which executes arbitrary code
on deserialisation. Anyone who can write to Redis then has remote code execution
on every worker. Restricting to JSON removes that entirely, and JSON is
sufficient because tasks carry only an id.

**Cost:** task arguments must be JSON-serialisable, which the id-only rule
already required.

---

# Milestone 6 decisions

## 41. Reporting only on lines the pull request changed

**Chosen:** static-analysis findings on lines outside the diff are discarded.

**Alternative:** report everything wrong with each changed file.

**Why:** a linter run over a changed file surfaces every problem in it, and most
of them predate the pull request. Posting those as review comments asks the
author to fix code they did not write, in a change that is not about it. That is
precisely how an automated reviewer becomes noise people mute -- and a muted
reviewer catches nothing at all. The mock fixture demonstrates it: the file has
an unused `json` import on line 1 that the diff never touches, and it is
correctly dropped.

**Cost:** a genuine problem introduced *by* the change but manifesting on an
untouched line is missed. The LLM stage, which sees the whole file, is better
placed to catch that class of thing anyway.

## 42. Only analysers that parse, never execute

**Chosen:** Ruff, invoked with `--isolated`.

**Alternative:** richer tooling -- mypy with imports resolved, ESLint with the
project's plugins, pytest collection.

**Why:** the input is code from a stranger's pull request. Any tool that imports
the module, resolves plugins from the repository, or honours a config file
checked into it is a remote code execution vector pointed at our worker. Ruff
parses source into an AST and never runs it. `--isolated` matters just as much:
without it a pull request could ship a `pyproject.toml` that disables the rules
which would have flagged it.

**Cost:** shallower analysis than a type checker with full import resolution,
and Python only. Adding a language means adding an analyser that satisfies the
same constraint.

## 43. Fetching whole files rather than analysing the diff

**Chosen:** fetch each changed file at the head commit and analyse it in full,
then filter findings to changed lines.

**Alternative:** analyse the diff hunks alone.

**Why:** a hunk is not a valid program. A linter given a fragment cannot resolve
imports, see enclosing scope, or tell an undefined name from one defined twenty
lines above the hunk. Analysing the whole file and filtering afterwards gets
accurate results and still reports only what the author is responsible for.

**Cost:** one extra API call per changed file, and files above the size limit
are skipped rather than partially analysed.

## 44. Refusing diffs that are too large

**Chosen:** hard limits on diff bytes, changed files and per-file size, raised
as a *permanent* failure.

**Alternative:** review whatever arrives, truncating as needed.

**Why:** the limit is not frugality. A 5,000-line diff produces a prompt no
model reads carefully, and what comes back is confidently vague -- worse than no
review, because it looks like one. Refusing with a clear reason is more honest.
The failure is permanent rather than transient because the diff will not shrink
on a retry.

**Cost:** genuinely large refactors get no review. A future version could review
them file by file rather than as one prompt.

## 45. A missing analyser fails loudly instead of finding nothing

**Chosen:** `AnalyzerUnavailableError` when the tool is not installed, and a
`failed_analyzers` list threaded through to the review context when one crashes.

**Alternative:** log a warning and return no findings, which is what the first
implementation did.

**Why:** this was found by running the worker rather than by testing it. Ruff
was a development dependency, so the production image did not have it; the
subprocess failed, the code returned an empty list, and the review reported
zero findings and success. **Zero findings is indistinguishable from clean
code.** A tool that silently downgrades itself while still claiming to have
checked is worse than one that refuses -- people trust it and should not.

**Cost:** a review can now fail for an operational reason rather than degrading.
That is the intended trade: an incomplete review says so.

## 46. Ruff runs with `--no-cache`

**Chosen:** disable Ruff's cache.

**Why:** also found only by running in the container. Ruff writes a cache
directory into the working directory by default; the image runs as an
unprivileged user and `/app` is root-owned, so it exited with a permission error
and produced nothing. Caching buys nothing here regardless -- each invocation
analyses one temporary file that is deleted immediately afterwards.

**Cost:** none in this usage.

## 47. Hand-written diff parsing

**Chosen:** parse unified diffs directly rather than adding a library.

**Alternative:** a package such as `unidiff`.

**Why:** DevPilot needs one fact from a diff -- which lines in the new file were
added or changed -- and that subset of the format is small and completely
specified. A hand-rolled parser is about a hundred lines, can be read in one
sitting, and is exhaustively tested. The line numbers it produces decide which
line a review comment lands on, so being able to see exactly how they are
derived is worth more than the dependency saved.

**Cost:** edge cases have to be found and handled ourselves. The tests cover the
ones that bite: `+++`/`---` headers that look like content, omitted hunk counts,
renames, binary files, and "\\ No newline at end of file".

---

# Milestone 7 decisions

## 48. The model is never asked for the risk score

**Chosen:** the LLM schema contains `summary` and `findings` and nothing else.
The 0-100 score is computed in Python from the validated severities.

**Alternative:** the response format in the specification includes `risk_score`,
so ask for it.

**Why:** a model asked to rate risk gives a plausible number that varies between
runs on byte-identical input. It cannot be unit tested, cannot be justified to a
user who asks why their pull request scored 72, and cannot be tuned without
editing a prompt and re-running everything. Deriving it from severities makes it
reproducible, auditable and adjustable by changing a constant.

The field is omitted from the schema entirely rather than requested and
discarded: asking for a number we throw away wastes tokens and invites the model
to reason about the wrong thing.

**Cost:** a small divergence from the letter of the specification, in service of
its stated intent ("use deterministic scoring rather than allowing the LLM to
arbitrarily determine the final score").

## 49. A severity floor on top of the weighted sum

**Chosen:** `score = clamp(max(weighted_sum x 5, floor_of_worst_severity))`, with
floors of 75/50/25/5 for critical/high/medium/low.

**Alternative:** the weighted sum alone, as specified.

**Why:** with the given weights, a pure sum makes eleven cosmetic findings (11)
outrank one critical one (10). That inverts the thing the score exists to
express. The floor guarantees the worst single finding puts the score in the
band it belongs to, and the sum still differentiates within that band. The
`describe_risk` labels use the same boundaries, so the words and the number can
never disagree.

**Cost:** two numbers to explain instead of one, and the scale factor is a
judgement call. Both are constants with tests pinning the properties that
matter.

## 50. Validating the model's claims, not just its JSON

**Chosen:** every finding is checked against the parsed diff. A finding citing a
file the pull request did not touch, or a line it did not change, is discarded
with a logged reason.

**Alternative:** trust the schema. The response is valid JSON with correct types,
after all.

**Why:** this is what "never trust LLM output without validation" has to mean in
practice. A model will confidently cite `src/auth/handler.py:412` for a pull
request that touched neither. Pydantic cannot catch it -- the path is a
well-formed string and the line a positive integer. Only the diff knows. Without
this check, DevPilot would post review comments on lines that do not exist,
which destroys trust in the tool faster than missing a bug does.

Rejected findings are counted rather than silently dropped: a model that
regularly invents paths is a prompt problem, and counting is the only way to
notice.

**Cost:** a genuine problem on an untouched line is discarded along with the
hallucinations. That is the same tradeoff already made for static analysis, and
for the same reason.

## 51. Confidence gates posting, not storing

**Chosen:** every validated finding is stored and shown on the dashboard;
only those at or above `llm_min_confidence_to_post` are posted to GitHub.

**Alternative:** discard low-confidence findings entirely, or post everything.

**Why:** these are different questions. A finding worth recording is not always
worth interrupting someone with, and a reviewer that posts its own guesses is
one people mute. Keeping them visible on the dashboard means a curious author
can still look, while the pull request stays quiet.

**Cost:** the dashboard shows more than GitHub does, which has to be explained
in the UI so the difference does not look like a bug.

## 52. Retrying only on invalid responses

**Chosen:** `request_review` retries when the model returns something unusable,
and lets every other error propagate to the worker.

**Why:** an invalid response is the one failure a retry genuinely fixes --
generation is stochastic, so the same prompt can produce conforming output next
time. A rate limit or a transient network failure is the worker's business,
because only the worker can record the attempt on the job row and schedule the
backoff. Retrying in two places would double the effective delay and hide
attempts from the job's history.

**Cost:** two retry mechanisms in the codebase, each with a clearly separate
job.

## 53. The mock provider restates the analyser rather than inventing a review

**Chosen:** in mock mode, findings are derived from static-analysis output, every
summary is prefixed `[Mock review - no language model was called.]`, and
confidence is pinned at 0.5 -- below the default posting threshold.

**Alternative:** return a plausible fabricated review so demos look better.

**Why:** a fabricated review is indistinguishable from a real one in the
database and on the dashboard. Someone would eventually screenshot it, or trust
it. Restating the analyser gives the pipeline real, correctly-anchored data to
carry end to end while making it impossible to mistake the output for judgement
about the code. The sub-threshold confidence is a second guard: even wired to a
live GitHub App, a mocked finding cannot be posted.

**Cost:** mock reviews are not interesting to look at. That is the point.

## 54. Structured output at request time, not parsed afterwards

**Chosen:** `client.messages.parse()` with the Pydantic model as the output
format, so the schema is sent with the request and the SDK returns a validated
instance.

**Alternative:** ask for JSON in the prompt and parse the reply.

**Why:** prompt-requested JSON arrives wrapped in prose, in fenced code blocks,
with trailing commentary, or subtly off-schema -- and every one of those is a
retry that costs another call. Constraining the format at request time makes
conformance a property of the request. `extra="forbid"` on the models becomes
`additionalProperties: false` in the emitted schema, which is what makes the
constraint strict rather than advisory.

**Cost:** ties the implementation to a provider that supports constrained
decoding. The `LLMProvider` Protocol keeps that dependency in one file.

## 55. Adaptive thinking, effort `high`

**Chosen:** `thinking: {type: "adaptive"}` with `output_config.effort` defaulting
to `high`.

**Why:** reviewing a diff is exactly the work reasoning helps with -- the model
has to hold the change in mind and consider what it breaks, rather than
pattern-matching the first suspicious line. Effort is the cost lever that trades
thoroughness for spend within one model, and code review is the workload where
it repays; `medium` is exposed in configuration for routine changes.

**Cost:** more tokens per review than a non-reasoning call. Both settings are
configuration, and `docs/llm-setup.md` documents them as the first thing to turn
down.

## 56. Only the worker holds the API key

**Chosen:** the model is called from the Celery worker; the API process never
constructs a provider.

**Why:** the API is internet-facing and handles unauthenticated webhooks. The
worker is not reachable from outside at all. Keeping the billing credential out
of the process with the larger attack surface is free, and it means the two can
be deployed with different secrets.

**Cost:** none. It falls out of the pipeline already living on the worker.

---

# Milestone 8 decisions

## 57. A mock embedder that produces meaningful distances

**Chosen:** feature hashing -- each token hashed to a dimension, the vector
L2-normalised -- so texts sharing vocabulary genuinely land near each other.

**Alternative:** random vectors, which is what most mock embedders return.

**Why:** random vectors make retrieval *run* while making it meaningless. Every
search returns arbitrary chunks, so nothing downstream can be judged and the
integration tests assert only that rows came back. Feature hashing is
deterministic, needs no network, and produces distances that mean something:
the tests can assert that a change reading `LOOKUP` retrieves the file defining
`LOOKUP`, which is the actual behaviour under test.

The limitation is stated in the module docstring rather than hidden: **this is
lexical, not semantic.** It matches shared words and will not connect
"authenticate" to "login". It is enough to develop and test against, and not a
substitute for a real embedding model.

**Cost:** two embedding implementations. The Protocol keeps them honest.

## 58. Voyage for embeddings, Claude for reviewing

**Chosen:** `voyage-code-3` for retrieval, Claude for the review itself.

**Why:** Anthropic has no embeddings endpoint and recommends Voyage, so a second
vendor is unavoidable rather than chosen. `voyage-code-3` is trained on code
specifically, which matters: a general-purpose text embedder treats source as
prose and retrieves on comments and identifier spelling rather than on what the
code does.

**Cost:** a second API key for anyone running in full live mode. Both default to
mock, so neither is needed to run the project.

## 59. Chunking on line boundaries with overlap

**Chosen:** 60-line chunks overlapping by 10, split on line boundaries.

**Alternative:** a fixed character count, which is simpler.

**Why:** a character split cuts through the middle of a function signature or a
string literal, and the fragment either misleads the model or is useless to it.
Line boundaries keep every chunk independently readable. The overlap means a
function spanning a boundary appears whole in one of the two chunks covering
it -- without it, the most interesting code is exactly the code that gets split.

**Cost:** ~17% more chunks than non-overlapping, so ~17% more embedding spend
and storage. Cheap next to retrieving half a function.

## 60. Content-hash deduplication on re-index

**Chosen:** hash each chunk's content; skip embedding anything already stored.

**Why:** embedding is the expensive part of indexing -- a paid API call per
batch -- and most of a repository is unchanged between runs. Hashing turns
re-indexing from "embed everything again" into "embed what changed", which is
the difference between a re-index costing pennies and costing what the first one
did. The integration test asserts a second index creates zero chunks.

**Cost:** a hash per chunk, which is nothing, and a `content_hash` column, which
is already the natural de-duplication key.

## 61. Deleting chunks whose content is gone

**Chosen:** after indexing, remove stored chunks whose hash is not in the current
set.

**Alternative:** only ever add.

**Why:** otherwise deleted code stays retrievable forever. The model is shown a
function that no longer exists, with a file path and line range that make it
look current, and reasons about the change as though it were still there. Stale
context is worse than no context because it is confidently wrong.

**Cost:** a delete per indexing run, and a full re-index is required to reclaim
space after a large deletion.

## 62. A distance ceiling on retrieval

**Chosen:** discard results beyond `retrieval_max_distance` (0.75 cosine).

**Why:** a nearest-neighbour search always returns its k nearest rows, however
far away they are. Without a ceiling, a change to coupon logic in a repository
containing nothing similar still retrieves six chunks, and the model is handed
irrelevant code presented as relevant context -- which is actively worse than an
empty section, because the prompt says this code is related.

**Cost:** the threshold is a tuning parameter, and too tight a value returns
nothing. The integration test pins both ends: the default retrieves, and 0.01
retrieves nothing.

## 63. Excluding the changed files from retrieval

**Chosen:** chunks from files in the diff are filtered out of results.

**Why:** they are already in the prompt as the diff. Retrieving them again
spends the context budget on duplicates instead of the surrounding code the
model cannot otherwise see -- which is the entire reason retrieval exists.

**Cost:** a chunk from a changed file that would have added context beyond the
diff hunk is lost. The hunks already carry several lines of surrounding context.

## 64. HNSW rather than IVFFlat

**Chosen:** an HNSW index on `embedding vector_cosine_ops`.

**Why:** IVFFlat computes its list centroids at build time from existing data,
so an index created on an empty table -- which is exactly what a migration does
-- is worthless until rebuilt after data lands. HNSW builds incrementally and is
useful immediately. The operator class matters as much as the index type: an
index built for a different distance operator is silently ignored by the
planner, which is a uniquely annoying way to lose performance.

**Cost:** HNSW uses more memory and builds more slowly than IVFFlat at large
scale. Neither is a concern at the size this indexes.

## 65. Retrieval degrades rather than fails

**Chosen:** a repository that has never been indexed, or an embedding provider
that is down, produces a review *without* repository context.

**Alternative:** fail the job.

**Why:** retrieval is an enhancement. The diff and the static-analysis output
are still there, and a review based on them is worth having. Failing the whole
job because an optional stage was unavailable trades a good review for no
review. The prompt says plainly when nothing was retrieved, so the model is not
left to assume it saw everything.

**Cost:** a silently degraded review is possible. The log line and the
`retrieved_chunks` count on every completed review make it visible.

## 66. Retrieved code is labelled "not under review"

**Chosen:** the prompt section for retrieved chunks says explicitly that the
code is context and must not be commented on.

**Why:** without it the model reports problems in the retrieved code -- often
real ones -- and validation then discards every one of them for citing lines
outside the diff. That wastes output tokens and, worse, means the model spent
its attention on code the author did not touch.

**Cost:** a few dozen words of prompt. It pays for itself in the first review.

## 67. One GitHub review, not a comment per finding

**Chosen:** every finding for a pull request is posted as a single review whose
inline comments are attached in one API call.

**Alternative:** `POST /issues/{n}/comments` once per finding.

**Why:** GitHub sends a notification per comment. Eight findings would be eight
emails, eight timeline entries, and eight chances to be muted. A review is one
notification containing eight comments, which is how a human reviewer behaves. It
is also atomic: either the whole review appears or none of it does, so a failure
halfway through cannot leave half a review on someone's pull request.

**Cost:** one large request instead of several small ones, and a single rejected
inline position fails the entire call. Positions are therefore validated against
the diff before the request is built.

## 68. Posting is idempotent through a stored review id

**Chosen:** `reviews.github_review_id` is written after a successful post, and a
review that already has one is never posted again.

**Alternative:** trust that the job runs once.

**Why:** it does not. `acks_late=True` means a worker killed after posting but
before committing will have its message redelivered, and GitHub itself redelivers
webhooks it believes failed. Without the marker, the visible symptom is a pull
request accumulating identical reviews -- the worst kind of bug, because it is
loud, public, and in someone else's repository.

**Cost:** a review posted but not recorded (the process dies between the two)
will post twice. That window is one statement wide, against a redelivery window
of minutes, so it turns a routine failure into a rare one.

## 69. Confidence gates posting, not storing

**Chosen:** low-confidence findings are persisted and shown on the dashboard, and
excluded from what is posted to GitHub.

**Why:** the two audiences have different costs of being wrong. On the dashboard
a weak finding is a row a user can ignore; on a pull request it is a comment
someone must read, evaluate and dismiss in front of their colleagues. A reviewer
that posts its guesses stops being read at all, and once that happens the good
findings go unread with the bad ones. Storing them anyway means the threshold can
be tuned later against real data instead of a fresh LLM bill.

**Cost:** two different views of one review, which the UI has to explain. The
dashboard labels which findings were posted.

## 70. A failed post does not fail the review

**Chosen:** if publishing raises, the review stays `completed` and the failure is
recorded on the job.

**Alternative:** mark the review failed and retry the whole task.

**Why:** by the time publishing runs, the expensive work is done and committed --
the diff was fetched, the LLM was paid for, the findings are in the database.
Retrying the task would redo all of it to fix the last and cheapest step. The
review is genuinely complete; only its delivery failed, and the dashboard shows
it either way.

**Cost:** a review can exist that the pull request never sees. It is visible in
the UI and in the job's error field, and `POST /reviews/{id}/publish` sends it
once the cause is fixed. *(That endpoint did not exist when this entry was
first written -- see entry 114 for how that was discovered.)*

## 71. Reviews are posted as COMMENT, never REQUEST_CHANGES

**Chosen:** `event: "COMMENT"` is hardcoded.

**Why:** `REQUEST_CHANGES` blocks merges under many branch protection settings.
An automated reviewer that can block a merge on a hallucinated finding will be
uninstalled the first time it does, and it will do it. Advisory output earns its
place; a gate has to be earned first. This is the same instinct as computing the
risk score in code rather than letting the model choose it (entry 21).

**Cost:** a genuinely critical finding does not stop the merge. The risk score and
the dashboard make severity visible; the decision stays with a person.

## 72. Repository identity is per owner, not global

**Chosen:** `repositories` is unique on `(owner_id, github_repo_id)` rather than
on `github_repo_id` alone (migration 0006).

**Why:** the original constraint quietly encoded "one DevPilot account per GitHub
repository". Two users who both have access to the same repository are completely
ordinary, and under the global constraint the second one's sync inserted nothing
and their dashboard was empty -- no error, no log line, just missing data. Every
other row is already scoped to an owner; the constraint was the one place that
was not.

**Cost:** the same repository is stored once per owner, so its metadata is
duplicated. That is the right shape for per-owner settings anyway, and the row is
small.

**How it was found:** a Playwright test that registers a *second* user. The whole
unit suite passed, because a bug in multi-tenancy needs two tenants to appear.

## 73. Pull request identity is per repository

**Chosen:** `pull_requests` is unique on `(repository_id, github_pr_id)`
(migration 0007).

**Why:** the same mistake as entry 72, one table over, and it stayed invisible
until four owners tracked one repository at once under load. The lesson is worth
more than the fix: a globally unique external id is almost always wrong in a
multi-tenant schema, because the id is unique in *GitHub's* namespace, not in
ours.

**Cost:** identical to entry 72.

**How it was found:** a Locust run. It needs concurrency and several accounts,
which is exactly the combination no unit test creates.

## 74. Insert and catch, rather than check then insert

**Chosen:** `_upsert_pull_request` attempts the insert inside a `SAVEPOINT` and,
on `IntegrityError`, re-reads the row that won.

**Alternative:** `SELECT` first, `INSERT` if absent.

**Why:** check-then-act is not atomic. Two webhook deliveries for the same pull
request arriving milliseconds apart both saw "no row", both inserted, and one got
an unhandled `IntegrityError` -- an HTTP 500 that GitHub then redelivered. The
unique constraint is the only authority here that is actually atomic, so the code
asks it instead of guessing. The `SAVEPOINT` matters: without it the failed insert
poisons the outer transaction and the recovery read cannot run.

**Cost:** an exception on a normal path, which reads oddly until you know why. It
is logged at info, not error, because a race here is expected.

**How it was found:** two HTTP 500s out of roughly 600 requests in a load test.

## 75. Fixed-window rate limiting, not a sliding log

**Chosen:** one Redis counter per client per window, expiring with the window.

**Alternative:** a sliding window log -- a sorted set of request timestamps.

**Why:** the fixed window is one `INCR` and one `EXPIRE` in a single pipeline. The
sliding log is a sorted set, a range delete, a cardinality check and a trim --
several round trips and unbounded memory per client -- to buy precision that
matters for metered billing and does not matter for stopping brute force. Its
known flaw is a burst of up to twice the limit across a window boundary, which is
an acceptable price for an order of magnitude less work per request.

**Cost:** that boundary burst. If DevPilot ever bills per request, this is the
first thing to replace.

## 76. The limiter fails open

**Chosen:** if Redis raises, the request is allowed and the failure is logged at
error level.

**Alternative:** fail closed -- refuse when the limiter cannot be consulted.

**Why:** failing closed means a Redis blip takes the entire API down. The limiter
exists to contain abuse, which is a bounded harm; refusing all traffic is an
unbounded one. Choosing to fail open is choosing which outage you would rather
have. For an authorization check the answer would be the opposite, and that
difference is the whole point.

**Cost:** an attacker who can disrupt Redis can also disable rate limiting. They
still face authentication, and the error log makes the state visible rather than
silent.

## 77. Webhooks and health are never rate limited

**Chosen:** `/api/v1/webhooks/*` and `/health` are exempt.

**Why:** GitHub decides its own delivery rate and retries anything it believes
failed, so a 429 does not reduce load -- it turns one delivery into several and
makes the burst worse. That endpoint is already cheap and idempotent, and it is
authenticated by HMAC signature rather than by volume. `/health` is exempt because
an orchestrator probing every few seconds would otherwise throttle itself into
declaring a healthy service dead, which is a rate limiter causing the outage it
was installed to prevent.

**Cost:** anyone holding the webhook secret gets an unlimited endpoint. They can
already create jobs directly; the limiter was never the control there.

## 78. Keyed on the user where there is one

**Chosen:** the limit is keyed on the authenticated user id when the request
carries a valid token, and on client IP otherwise. `X-Forwarded-For` is honoured
only when `trust_proxy_headers` is set.

**Why:** keying purely on IP punishes shared addresses -- an office or campus NAT
means one noisy client exhausts everyone's allowance. The token is *decoded and
verified*, not merely read, so a forged one cannot claim someone else's bucket.
And `X-Forwarded-For` is a client-supplied string: trusting it unconditionally
would let any caller reset their own limit by inventing an address, so it is read
only where a proxy is known to overwrite it.

**Cost:** one signature verification per request on the limiter's path, which is
cheap, plus a flag that must be set correctly in production. Setting it wrong
fails safe -- everyone shares the proxy's IP.

## 79. Security headers in application middleware, not only in nginx

**Chosen:** `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and a
`default-src 'none'` CSP are set by FastAPI middleware.

**Alternative:** set them at the reverse proxy.

**Why:** the proxy is deployment-specific and the API is not. Headers set in the
application hold when the API runs directly, in Compose, under a test client, or
behind a proxy someone else configured -- and they are covered by tests, which a
proxy config is not. The CSP is maximally restrictive because the API serves JSON:
it has no scripts, styles or frames to allow, so anything the policy permits is
pure attack surface. The frontend is a separate origin with its own policy.

**Cost:** duplicated headers if the proxy also sets them, which is harmless.

## 80. HSTS is off by default

**Chosen:** `Strict-Transport-Security` is sent only when `enable_hsts` is set.

**Why:** the header is meaningless over plain HTTP and actively harmful locally --
a browser that receives it on `localhost` will refuse plain HTTP to `localhost`
for the whole `max-age`, across every project on that machine, and clearing it is
obscure. Enabling it is a decision for whoever actually terminates TLS.

**Cost:** it has to be remembered at deploy time, so the deployment checklist
lists it.

## 81. End-to-end tests run against the real stack

**Chosen:** Playwright drives a browser against the actual API, Postgres, Redis
and worker in Compose. No mocked network layer.

**Alternative:** mock the API in the browser and assert the UI renders.

**Why:** a mocked API tests that the frontend renders what the frontend was told
to expect, which is a tautology whenever the contract has drifted. Every bug these
tests actually caught -- the per-owner repository constraint most of all -- lived
in the seam between layers, and a mock is precisely a decision to stop testing
that seam. The API runs in mock GitHub and mock LLM mode, so the test is free and
deterministic while still being a real HTTP call to real code.

**Cost:** slower, and it needs the stack up. It is a separate CI job for that
reason, gated behind the unit jobs so a type error fails in seconds rather than
after a browser download.

## 82. A 429 in the load test counts as a success

**Chosen:** the Locust profiles mark rate-limited responses `success()`.

**Why:** the limiter refusing a request is the system working. Counting those as
failures produces a run whose failure rate measures how hard you pushed rather
than whether anything is broken -- and it buries the two real HTTP 500s from entry
74 under hundreds of expected 429s. Registration in `on_start` retries with
backoff for the same reason: a throttled sign-up leaves a virtual user with no
token, so every later request returns 401 and the run reports a wall of failures
that say nothing about read capacity.

**Cost:** the profiles are more code than a naive script. To measure raw capacity
instead, the run sets `DEVPILOT_RATE_LIMIT_ENABLED=false`.

## 83. Rate limiting is disabled in the test suite

**Chosen:** the `settings` fixture sets `rate_limit_enabled=False`; the limiter's
own tests enable it explicitly against an in-memory fake.

**Why:** tests share one Redis, so they share a window, and the four-hundredth
test to make an HTTP call fails for reasons that have nothing to do with it -- a
failure that moves when tests are reordered, which is the worst kind to debug. The
behaviour is not untested; it is tested in one place where the counter is
deterministic and inspectable.

**Cost:** the middleware runs disabled in most tests, so an interaction between it
and something else would not be caught there. The Compose stack runs it enabled,
and the load tests exercise it under real concurrency.

## 84. A separate production Compose file, not an override

**Chosen:** `docker-compose.prod.yml` is a complete, standalone file.

**Alternative:** the idiomatic `-f docker-compose.yml -f docker-compose.prod.yml`
override pair.

**Why:** an override can add keys and replace them, but it cannot *remove* them.
The two things production must not have are the source bind mounts and
`--reload`, and both would survive the merge -- so the file whose entire purpose
is to exclude them would fail at exactly that. There is a second reason: reading
one file top to bottom tells you what runs in production. Reading a merged pair
means simulating Compose's merge rules in your head, and the cost of getting that
wrong is a production container running whatever is on the deploy host's disk.

**Cost:** the two files share structure, so a change to the database image has to
be made twice, and they can drift. Mitigated by YAML anchors *within* the
production file, which is where the dangerous duplication actually was -- the API,
worker and migration job now cannot disagree about which database they use.

## 85. Migrations as a one-shot job, not on API startup

**Chosen:** a `migrate` service runs `alembic upgrade head` and must exit 0 before
`api` and `worker` start.

**Alternative:** run migrations in the API's startup hook.

**Why:** the startup hook is fine with one process and wrong with four. Each
uvicorn worker would run the upgrade against the same database simultaneously;
Alembic takes a lock, so three of them block until the first finishes, and a
migration slower than the startup timeout turns into a failed deploy that looks
like a crash loop. A one-shot job runs exactly once, and failing it fails the
deploy *loudly* -- which is the right outcome, because serving traffic against a
schema the code does not expect is worse than serving no traffic.

**Cost:** one more service in the file, and a deploy has an explicit ordering
step. `condition: service_completed_successfully` expresses it in Compose rather
than in a shell script.

## 86. Four uvicorn workers in one container, not four containers

**Chosen:** `--workers 4` inside the `api` service.

**Alternative:** four single-worker containers behind a load balancer.

**Why:** at one host, uvicorn's own process manager already provides what the
extra containers would -- parallelism past the GIL, and a supervisor that
restarts a dead worker. Four containers would need a load balancer purely to
distribute across them, which is infrastructure bought before the problem. The
constraint worth knowing is that each worker opens its own connection pool, so
`workers x DB_POOL_SIZE` must stay under Postgres `max_connections`; at the
shipped values that is 20 of 100.

**Cost:** the container is one restart unit, so a deploy cycles all four workers
together. Separate containers would allow rolling replacement, which is the right
move once a deploy outage stops being acceptable.

## 87. CI runs the migrations against a real PostgreSQL

**Chosen:** the backend job starts a `pgvector/pgvector:pg16` service and runs
`alembic upgrade head` before the tests.

**Alternative:** create the schema from `Base.metadata.create_all()`, which is
faster and needs no service.

**Why:** `create_all` tests the models, not the migrations, and the migrations are
what production actually runs. CI is the only place they execute before a deploy
does -- so a migration that is valid Python and invalid SQL would otherwise be
discovered by the deploy. Using the pgvector image rather than stock Postgres
matters for the same reason: on stock Postgres every retrieval test skips, and a
skipped test is not a passing one.

**Cost:** a slower job, and a service container to wait on. The offline
`--sql` drift test in `test_migrations.py` still runs everywhere with no
database, so the fast feedback did not go away.

## 88. Production images are built in CI but not pushed

**Chosen:** the `images` job builds `backend/Dockerfile` and
`frontend/Dockerfile` on every run and pushes nowhere.

**Why:** the Compose development stack builds a *different* target -- the frontend
runs Vite, not nginx -- so nothing else in CI proves the production images still
build. That failure is worth catching on a pull request rather than at deploy
time. Pushing is a separate decision requiring a registry and credentials, and
this project deploys by building on the host.

**Cost:** roughly a minute per run, largely eliminated by the GitHub Actions
build cache. **Revisit when:** there is a registry to push to, at which point this
job grows a tag and a login.

## 89. Nothing binds to a public interface

**Chosen:** in production, Postgres and Redis publish no host ports at all, and
`api` and `frontend` bind to `127.0.0.1`.

**Why:** a published port is reachable from the internet the moment the host
firewall is wrong, and host firewalls are wrong more often than anyone admits --
Docker's own iptables rules have historically bypassed `ufw`. Binding to loopback
means the reverse proxy on the same host can reach the service and nothing else
can, which does not depend on a firewall being right. TLS terminates at that
proxy: certificate renewal, HTTP/2 and redirects are its job, and reimplementing
them in uvicorn would be worse at all three.

**Cost:** you cannot attach `psql` from your laptop without an SSH tunnel. That is
the intended difficulty.

*Superseded in part by entry 120: TLS now terminates inside the stack, and the
API and frontend publish no host ports at all.*

## 90. Every container caps its logs

**Chosen:** `max-size: 10m`, `max-file: 5` on all six services.

**Why:** the default `json-file` driver is unbounded. One chatty container fills
the host disk, and then *every* service fails -- Postgres first, and in a way that
looks like a database bug rather than a disk problem. Two lines of configuration
convert a total outage into a rotated log.

**Cost:** 50 MB per service on disk, and old logs are gone. Anything that must
outlive rotation should be shipped off the host anyway.

## 91. Workers recycle after 100 tasks

**Chosen:** `--max-tasks-per-child=100`.

**Why:** a long-lived Python process that leaks even slightly per review will
eventually reach the container memory limit and be OOM-killed mid-task, at an
unpredictable moment, taking a review with it. Recycling makes that same reclaim
happen predictably, *between* tasks, where it costs nothing. It is insurance
against a leak rather than a claim that one exists.

**Cost:** a process start every 100 tasks -- a fraction of a second against
reviews that take tens.

## 92. Redis refuses writes rather than evicting

**Chosen:** `--maxmemory-policy noeviction` with an explicit `maxmemory`.

**Why:** the default (`noeviction` for a bare server, but `allkeys-lru` in many
images and hosted configurations) would let Redis silently discard queued Celery
messages under memory pressure. A dropped job is a review that never happens and
never errors, which is the hardest possible failure to notice. Refusing the write
surfaces the problem at enqueue time, where it is visible and retryable. The
`maxmemory` bound exists so this is a Redis error rather than the kernel killing
the container.

**Cost:** a full Redis rejects new jobs instead of quietly shedding old ones. That
is the correct trade for a queue and the wrong one for a cache, which is why the
rate-limit counters -- the only cache-like data here -- expire on their own.

## 93. The worker healthcheck runs through a shell

**Chosen:** `test: ["CMD-SHELL", "celery ... inspect ping -d celery@$$HOSTNAME"]`.

**Alternative (and the original):** the exec form, `["CMD", "celery", ..., "-d",
"celery@$$HOSTNAME"]`.

**Why:** the exec form runs no shell, so nothing expands `$HOSTNAME`. Celery
received a literal node name of `celery@$HOSTNAME`, no worker answered to it, and
the probe failed with `No nodes replied within time constraint` -- on a worker
that was consuming tasks perfectly well the entire time. Compose's `$$` escape
made this harder to see, because it looks exactly like the shell escaping you
would write if a shell were involved.

The failure mode is worse than a missing healthcheck. Docker reported the
container `unhealthy`, so in production an orchestrator would restart a working
worker every few minutes -- mid-review, forever -- and the logs would show
nothing wrong, because nothing was.

`-d` is kept rather than pinging every node on the broker: without it the probe
passes whenever *any* worker replies, so scaling to three workers would mean two
dead ones still reporting healthy because the third answered.

**Cost:** one shell process per probe, every thirty seconds.

**How it was found:** looking at `docker ps` output during Milestone 12 and
noticing the worker had been `(unhealthy)` for twenty-six minutes. No test
asserts on a Compose healthcheck, and the review pipeline had just been verified
end to end through that same container -- which is precisely why it went
unnoticed. It is the fifth bug in this project that was invisible to a green test
suite and obvious the moment someone read what the running containers said about
themselves.

## 94. Two real LLM providers behind one Protocol

**Chosen:** `DEVPILOT_LLM_MODE` is `mock | anthropic | gemini`, and both real
providers implement the same `LLMProvider` Protocol.

**Alternative:** pick one vendor and call its SDK from the pipeline.

**Why:** the Protocol was written in Milestone 7 on the argument that the
pipeline should not know which vendor answers. Adding Gemini is what tested that
claim, and the answer was one new file, one factory branch, and no change
whatsoever to the pipeline, the prompt, the validation pass or the risk score.
An abstraction that has never been used twice is a guess; this one is now
evidence.

The practical driver is cost. Gemini has a free tier and Claude does not, so
without a second provider the project is only runnable by someone willing to pay
per review -- which for a portfolio project means it is mostly not run at all.

**Cost:** two providers to keep working, and two sets of failure semantics to
map. The mapping is where the real work is (entry 96), and it is covered by
tests that need no key and no network.

## 95. The mode names the provider, rather than a separate `live` switch

**Chosen:** one setting whose values are `mock`, `anthropic`, `gemini`.

**Alternative:** keep `LLM_MODE=mock|live` and add `LLM_PROVIDER=anthropic|gemini`.

**Why:** "real or mocked" and "which vendor" are the same decision, and two
settings that encode one decision can contradict each other --
`LLM_MODE=mock` with `LLM_PROVIDER=gemini` has no meaning, and something has to
decide which wins. One setting with three values cannot be in an invalid state.

The model name follows the provider unless it is set explicitly, because the
failure it prevents is nasty: switching mode to `gemini` and forgetting
`LLM_MODEL` sends `claude-opus-5` to Google and fails with "model not found",
which reads like a broken integration rather than a one-line mistake.

**Cost:** renaming `live` broke nothing here because the project was not yet
deployed. In a released product this would need a deprecation period.

## 96. Gemini's schema is the same Pydantic model

**Chosen:** `response_schema=LLMReview` -- the same class sent on the Anthropic
path -- with `response_mime_type="application/json"`.

**Alternative:** hand-write the OpenAPI-subset schema Gemini documents.

**Why:** Gemini accepts a narrower schema dialect than the one Pydantic emits: no
`$defs`/`$ref`, no `additionalProperties`, and `nullable: true` instead of
`anyOf`. That looked like it needed a translation layer, so it was worth
checking rather than assuming -- and the SDK already performs exactly that
conversion, inlining the definitions, dropping `additionalProperties` and
rewriting optionals, while preserving the enums and the length and range
constraints.

Keeping one schema matters beyond convenience: it means a review cannot differ in
shape depending on who generated it, so every downstream guarantee -- the
severity enum, the confidence range, the 25-finding cap -- holds identically for
both vendors.

**Cost:** dependence on the SDK's conversion being faithful. A test asserts the
Pydantic model itself is what gets sent, so a future SDK that stopped converting
would fail loudly rather than silently drop a constraint.

## 97. Rotating a pool of API keys

**Chosen:** the Gemini provider draws keys from an `ApiKeyPool` that
round-robins, parks a key that returns 429 until its cool-down expires, and
permanently retires one that returns 401 or 403.

**Why:** free-tier quota is metered per key, so rotation is the difference
between a pipeline that runs and one that spends its day rate-limited.

Three details each prevent a specific failure:

* **Round-robin on every request, not only on failure.** Limits are per *minute*,
  so the goal is to spread load. Failover-only rotation pins key one at its
  limit while the rest sit idle.
* **Cool-down on 429, using the provider's own `retryDelay`.** Without it,
  rotation walks back onto the exhausted key a few requests later. Guessing a
  fixed wait either wastes quota or fails again immediately.
* **Disable on 401/403.** A revoked or mistyped key never starts working, so
  retrying it every Nth request converts a configuration mistake into a
  permanent one-in-N failure rate.

Keys never reach the logs; a key is identified by its position in the pool, so a
log line can say *which* key misbehaved without putting the secret into a log
aggregator.

**Cost:** the pool is per-process, and Celery's prefork workers are separate
processes, so N workers hold N independent views and spread load approximately
rather than exactly. The failure mode is benign and self-correcting -- a second
process tries a parked key, gets a 429, and parks it too. Moving the cool-down
into Redis, which this project already runs for rate limiting, is the fix if
quota ever gets tight enough to justify the coupling.

**Revisit when:** worker concurrency is high enough that the approximation
actually wastes quota.

## 98. An exhausted key pool is a rate limit, not a failure

**Chosen:** when every key is cooling down, the provider raises
`LLMRateLimitError` carrying the shortest remaining wait.

**Alternative:** raise a configuration error, or fail the job.

**Why:** the worker already knows how to handle a rate limit -- back off for the
stated interval and retry -- and quota returns on its own. Failing the job would
discard work that will be perfectly possible in thirty seconds. Reporting the
*shortest* wait rather than the longest matters for the same reason: waiting 90
seconds when a key frees up in 30 wastes a minute of a quota that is already
scarce.

**Cost:** a genuinely misconfigured deployment (every key invalid) retries a few
times before giving up, rather than failing immediately. The pool distinguishes
the two cases in its message, and a rejected key is disabled rather than cooled,
so that path ends quickly.

## 99. Gemini's HTTP 200 failures are checked explicitly

**Chosen:** the provider inspects `prompt_feedback.block_reason` and the
candidate's `finish_reason` before reading any content, and re-validates the
parsed result against `LLMReview`.

**Why:** a safety block and a truncated generation both arrive as **HTTP 200**.
A provider that only maps status codes treats a refused review as a successful
one and stores an empty summary as though the model had nothing to say.

The three outcomes are deliberately different exceptions, because the correct
response differs: a refusal is permanent (re-asking an identical refused prompt
gets refused again), a truncation is a configuration problem and says so by
naming `DEVPILOT_LLM_MAX_OUTPUT_TOKENS`, and a malformed body is worth one more
attempt because generation is stochastic.

Re-validating rather than trusting `response.parsed` follows the project's
standing rule that model output is checked at the boundary: the SDK hands back a
plain dict when it cannot instantiate the model, and a review that skipped
validation is exactly what this pipeline refuses to build on.

**Cost:** more code than reading `response.text`, and it depends on finish-reason
names that could change. Unknown reasons fall through to "stopped unexpectedly"
rather than being treated as success.

## 100. Gemini gets the schema in the prompt, not as a response schema

**Chosen:** the Gemini provider sends `response_mime_type="application/json"` and
puts the JSON Schema, generated from `LLMReview`, into the system instruction.
The response is validated against `LLMReview` on the way back.

**Alternative (and the first three attempts):** Gemini's structured-output
fields, which are the direct counterpart to the Anthropic path's
`output_format=LLMReview`.

**Why:** the schema could not be made to work, and the API would not say why.

1. Passing the Pydantic model sends `additionalProperties` (from
   `extra="forbid"`), which the Developer API has no field for:
   `400 Unknown name "additional_properties"`.
2. With that stripped recursively, `response_schema` still fails on Gemini 3.x
   with a bare `400 INVALID_ARGUMENT` naming no field.
3. `response_json_schema`, the SDK's documented modern replacement, fails the
   same way.
4. A *trivial* schema is accepted on the same model, and so is a nested
   object-in-array with length caps. So the rejection is some specific feature
   of this schema -- enums, `anyOf` nullables and nested `$defs` are the
   remaining candidates -- and each additional probe costs free-tier quota to
   learn one bit.

Binary-searching an undocumented vendor dialect would produce a schema that is
correct today and breaks at the next model generation, which is the same bet
that had already failed twice. Putting the schema in the prompt works on every
Gemini model, and `response_mime_type` -- which *is* accepted everywhere -- still
prevents the most common parse failure, a JSON object wrapped in prose or a
markdown fence.

The weaker guarantee is real: the model is asked rather than constrained. It is
backed by the two mechanisms this project already had for exactly this --
validation of every response against `LLMReview`, and
`llm_max_validation_retries` when generation wanders. The rule was never that
the provider guarantees the shape; it was that *we* check it. Anthropic's
`output_format` was a bonus, not the guarantee.

The schema text is generated from `LLMReview` rather than hand-written, so a
prompt that promises one shape while the validator demands another -- a bug that
surfaces only as inexplicable validation failures -- cannot happen.

**Cost:** a few hundred prompt tokens per review, and more malformed responses
than a constrained decoder would produce. Measured on the first real call: 808
input tokens, and the review was correct.

**Revisit when:** Gemini documents which schema features it accepts, or the SDK
starts reporting which field was rejected.

## 101. A narrow JSON repair for stray backslashes

**Chosen:** when a response fails to parse, invalid escape sequences are doubled
and the parse is retried once. Only then, and never on output that already
parses.

**Why:** the very first successful Gemini call returned a complete, correct
review -- SQL injection at line 4, plaintext password comparison at line 6, both
right -- and it was thrown away because one backslash inside a string was not
doubled. A reviewer quotes code constantly, and code is full of backslashes:
regexes, Windows paths, LaTeX. This is not a rare accident; it is a systematic
tendency, so retrying would likely reproduce it while spending more quota.

Doubling is provably what the model meant, because a literal backslash is the
only thing an invalid escape *can* have been -- JSON permits exactly
`" \ / b f n r t uXXXX`, and everything else is a syntax error rather than an
ambiguity.

The leniency is deliberately scoped to **JSON syntax**, not to what a review may
contain: the repaired text is still validated against `LLMReview`, so a
well-formed document with wrong content is rejected exactly as before. A test
pins that distinction.

**Cost:** a repair path that could, in principle, alter a string the model
intended differently. The no-op-on-valid-input test bounds the risk, and the
repair is logged at warning level, so if it starts firing often the answer is to
fix the prompt rather than lean harder on the repair.

## 102. Testing a wire format means testing the wire, not the object

**Chosen:** the Gemini schema and prompt tests assert on serialised output, and
checked **both** the `additional_properties` and `additionalProperties`
spellings while that code existed.

**Why:** the original test asserted the converted schema contained no
`additionalProperties` -- and passed, while the live call failed on exactly that
field. The SDK names it `additional_properties` in Python and normalises it to
camelCase on the way out, so checking one spelling reported a clean schema while
the other spelling travelled. A green test beside a broken request is the worst
possible signal, because it actively discourages looking at the right place.

The general lesson outlived the specific fix: when the thing under test is a
*wire format*, asserting on the in-memory object tests the wrong artefact.

**Cost:** string matching against serialised output is cruder than attribute
assertions and would not catch a merely-wrong value. Pairing it with structural
assertions covers both.

## 103. `PYTHONPATH=/app` so the mounted source is the one that runs

**Chosen:** the image sets `PYTHONPATH=/app`.

**Why:** the image contains **two copies** of the application. `pip install .`
puts one in `site-packages`, and `COPY app ./app` puts another at `/app/app`.
Which one `import app` finds depends on `sys.path[0]`, which Python sets to the
directory of the script being run:

* `python -c "import app"` -- `sys.path[0]` is the working directory, so the
  bind-mounted `/app/app` wins.
* `python scripts/check_llm.py` -- `sys.path[0]` is `/app/scripts`, so
  `site-packages` wins and the code that runs is whatever was baked at build
  time.

The failure this produces is genuinely nasty: edits to mounted source appear to
do nothing, there is no error, and a check run one way disagrees with the same
check run the other way. It cost several rounds of debugging a fix that was
already correct -- the fix simply was not the code being executed.

Putting `/app` on `PYTHONPATH` places it ahead of `site-packages` for *every*
entry point, so the mounted source is authoritative for uvicorn, celery and any
script alike. In production, where nothing is mounted, the two copies are
identical and this changes nothing.

**Alternative:** stop installing the application into `site-packages` at all,
leaving only `/app/app`. That removes the duplicate rather than ordering around
it, and is the tidier answer -- but `pip install .` is also how the dependencies
in `pyproject.toml` get resolved, so it would mean maintaining a separate
requirements file purely to avoid installing one package.

**Cost:** the duplicate still exists; `PYTHONPATH` only decides which wins.
**Revisit when:** the build is restructured, at which point deleting the
installed copy is the better fix.

## 104. The test suite is hermetic against `DEVPILOT_*` variables

**Chosen:** a session-scoped autouse fixture removes every `DEVPILOT_*`
environment variable for the duration of the run.

**Why:** `Settings(_env_file=None)` stops pydantic-settings reading `.env`, but
it still reads `os.environ` -- so a test asserting on a *default* is really
asserting on whatever the surrounding shell exports. Two tests in
`test_config.py` did exactly that. They passed on every developer machine and
failed the moment CI set `DEVPILOT_SECRET_KEY` at workflow level, reporting that
the production secret guard was broken when the guard was fine.

That is among the worst failures to debug: the test is correct, the code is
correct, and only the environment differs. Worse here than usual, because
GitHub's job-log API answers 403 without a token, so the failure could not be
read directly -- it had to be reproduced by re-running the suite locally with
CI's exact variables exported.

**Cost:** a test that genuinely wants an environment variable must set it
itself, via `monkeypatch`. That is the correct way to write such a test anyway.
`DEVPILOT_TEST_DATABASE_URL` is read into a module constant at import time, so
integration tests still find the database CI points them at.

## 105. CI sets no secret key at all

**Chosen:** the workflow's `env:` block contains only `DEVPILOT_ENVIRONMENT: ci`.

**Why:** nothing in CI needs a signing key. The tests build their own `Settings`,
and only `production` demands a real secret. The key was added defensively, and
"defensively" turned out to mean "visible to every test process", which is what
broke the build.

Even with the suite now hermetic, it stays out: an unused secret in a workflow
file is only ever a liability, and a placeholder that looks like a credential
invites someone to replace it with a real one.

**Cost:** none. If a future job genuinely needs a key, it belongs in that job's
`env:` rather than the workflow's.

## 106. The end-to-end stack gets a larger auth rate limit

**Chosen:** the e2e environment sets `DEVPILOT_RATE_LIMIT_AUTH_REQUESTS=500`.

**Why:** every browser test registers its own account -- deliberately, so tests
cannot interfere through shared state -- and they all arrive from one IP. The
suite makes fourteen authentication requests in well under a minute, and the
limit is ten, so the last four received `429` and four tests failed on a
timeout waiting for a dashboard that was never going to load.

Both halves of that are working as intended: ten attempts per minute per IP is
the right default against credential stuffing, and per-test registration is the
right way to keep tests independent. The mismatch is that a test runner is not
the client the limit was written for.

Raised rather than **disabled**, which is the part worth defending: the
middleware stays in the request path, so a limiter that miscounted, or one that
started refusing requests it should allow, would still fail this job. Disabling
it would remove the only place the limiter is exercised against a real browser
over a real network.

**Cost:** the e2e stack is configured slightly unlike production, and that
difference has to be remembered in two places -- the CI workflow and the README.
Both say why.

**How it was found:** the first CI run in which the end-to-end job executed at
all. It had been skipped on every previous run because the backend job failed
first, so this had been latent since the tests were written.

## 107. An installation id from a request body is checked against its owner

**The bug:** `POST /api/v1/repositories/sync` took `installation_id` from the
request body and passed it straight to GitHub. Nothing verified that the
installation belonged to the caller.

That is a broken-authorization hole, and a nastier one than it first looks,
because **DevPilot authenticates to GitHub as the App, not as the user**. The
App holds a private key and can mint an installation token for *any* of its
installations. So an authenticated DevPilot user could send somebody else's
installation id and have DevPilot fetch, on their behalf, that person's
repository list -- names, privacy flags, default branches -- and store it under
their own account. Installation ids are sequential nine-digit integers, so
guessing is no obstacle. `GET /repositories/installations` made it easier still:
it returned **every** installation of the App, complete with account logins, to
any signed-in user.

The consequences compound. A repository row the attacker now owns can be indexed
via `POST /repositories/{id}/index`, which fetches **file contents** -- so the
hole reaches source code, not just metadata.

**Chosen:** `installations_for(user, client)` filters GitHub's list to
installations whose account id equals the user's linked `github_id`, and sync
refuses anything not in that list. Both endpoints share the one definition of
"yours", so they cannot drift apart.

**Why the account id and not the login:** logins are renameable and reusable;
numeric ids are not. Matching on a login means a renamed account can inherit
someone else's installations.

**Why 404 and not 403:** confirming that an installation exists is itself
information the caller has not earned. A real id they do not own answers exactly
like an invented one, and a test asserts the two responses are byte-identical.

**Why an unlinked user owns nothing:** with no `github_id` there is nothing to
match against, so DevPilot cannot know any installation is theirs. Returning
empty is the honest answer rather than a convenient one.

**Cost:** installations owned by an *organisation* are now refused, because the
installing account is the org and the user's `github_id` is their personal id.
The correct fix for that is `GET /user/installations` called with the user's own
OAuth token, which is scoped by GitHub itself -- but DevPilot deliberately does
not store user access tokens, keeping only the identity. Supporting orgs
therefore means storing a token, which is a real security tradeoff of its own and
was not worth making silently. **Revisit when** an organisation actually needs
it.

**How it was found:** connecting a real GitHub App. A sync of 42 repositories
had plainly succeeded, yet `select ... from users where github_id is not null`
returned no rows -- the sync had worked for an account with no linked GitHub
identity at all, which it should never have been able to do. No test caught it
because every test supplied the one installation the mock exposes; the hole is
only visible when the id and the caller can disagree.

## 107. An installation can only be synced by the identity that owns it

**Chosen:** `POST /repositories/sync` verifies that the requested installation
belongs to the caller's linked GitHub identity, and `GET /repositories/
installations` returns only those installations. A user with no linked identity
owns none.

**What it was before:** the sync endpoint took an `installation_id` from the
request body and used it. `list_installations` returned every installation of
the App to any signed-in user.

**Why it mattered:** DevPilot authenticates to GitHub as the *App*, not as the
user, so it will mint an installation token for any id it is handed. Together
the two endpoints were a complete attack: list everyone's installations, pick
one, sync it, and read the names and privacy flags of somebody else's private
repositories into your own account -- then index them and read their contents.
Installation ids are sequential integers, so the listing was a convenience
rather than a requirement.

**How it was found:** not by a test. The first real user synced 42 repositories
while their `github_id` was still `NULL`, which should have been impossible. It
took a real installation to notice, because in mock mode there is one user and
one installation and nothing to cross.

**Cost:** personal installations only. The check compares the installation's
`account.id` to the user's `github_id`, which is exactly right for an App
installed on a user account and wrong for one installed on an organisation the
user merely belongs to. Supporting that needs an organisation-membership check
and a permission the App does not currently request. Documented as a
limitation; it is the next thing to do if anyone installs on an org.

## 108. The OAuth link starts with a fetch, not a link

**Chosen:** `GET /github/authorize` returns `{"authorize_url": ...}` as JSON.
The frontend fetches it with the Bearer token and then navigates.

**What it was before:** a `307` redirect -- and a frontend comment noting that
nothing could use it, because the route needs the token and a plain navigation
cannot send one. So there was no *Connect GitHub* button at all. The feature
was unfinished, and the only reason anything worked was the hole in entry 107.

**Why the two-hop shape:** the route has to know *which* user is linking, and
that comes from the token. An `<a href>` cannot carry a header. Alternatives --
putting the token in the query string, or minting a one-time link -- either leak
the token into browser history and server logs, or add a second token type to
manage. Fetching the URL and then navigating costs one round trip and nothing
else; the signed `state` inside the URL still ties the callback to the user.

**Cost:** a button that does a fetch before navigating feels marginally slower
than a link. Nobody will notice.

## 109. Mock mode completes the OAuth round trip locally

**Chosen:** in mock mode the authorize URL points at DevPilot's *own* callback
with a placeholder code. The callback then runs unchanged: state verified, code
exchanged through the mock client, identity recorded.

**Alternative:** in mock mode, write the identity straight onto the user and
skip the flow.

**Why:** the state check is the one security-relevant step in linking, and a
browser is the only place it is exercised end to end. Skipping it in the only
mode a browser test can run in would leave it untested exactly where it matters.
Routing through the real callback means the e2e suite proves the whole thing
works, with no GitHub account involved.

The mock's OAuth identity was also changed to be the mock installation's owner.
Previously they were different accounts -- an organisation owned the
installation, a user did the linking -- which under entry 107 means the linked
user could never sync anything. The mock now models a personal installation,
which is the shape a developer running this locally actually has.

**Cost:** mock mode has a code path production never takes. It is one `if` in
`build_authorize_url`, and it is the reason the browser tests can test linking.

## 110. Browser tests that need GitHub share one account

**Chosen:** the e2e tests that link and sync sign in as a single deterministic
account (`e2e-github-owner@example.com`), registering it on first use. Every
other test still registers a fresh account.

**Why:** a GitHub identity can be linked to exactly one DevPilot account -- a
`UNIQUE` index, and a correct one, since otherwise anyone could attach your
GitHub identity to their account. Mock mode has exactly one identity. So tests
that each register a fresh account and then all try to link it are modelling
something impossible, and the second one is refused with `409`, correctly. The
constraint was right; the test design was wrong.

Sharing one account is what a real person with one GitHub login does. The link
persists between runs, so the helper is idempotent -- and it waits for the
status to load before deciding, because checking synchronously read "not
connected" on every visit and then waited for a button that would never appear.

**Cost:** those tests share state through the database and run in order. They
already had to, since the suite runs serially against one backend.

**How it was found:** the leftover link from a failed run. A random account from
an earlier run still held the identity, so the owner account could not claim it.
CI starts from an empty database and cannot hit this; locally it needed one
manual `UPDATE`.

## 111. Vite polls for file changes inside Docker

**Chosen:** `server.watch.usePolling`, enabled by `CHOKIDAR_USEPOLLING=true` in
the Compose environment.

**Why:** filesystem change events do not cross a Windows or macOS bind mount.
The file inside the container changes, `inotify` never fires, and Vite keeps
serving its cached transform of the old file. The failure is invisible: the dev
server is up, the page loads, hot reload appears to work, and the code on
screen is stale. Every frontend edit in this project had been going through a
container that was not serving it.

**How it was found:** a Playwright test waiting for a button that the source
file contained and the served module did not. Verified directly: the file in
the container had the change; `GET /src/pages/SettingsPage.tsx` from the dev
server did not.

**Cost:** polling costs CPU proportional to the file count, which is why it is
opt-in rather than always on. A native Linux checkout, where events work, is not
made to pay for it.

## 112. A failed review can be retried without pushing a commit

**Chosen:** `POST /pull-requests/{id}/retry` queues a fresh attempt when the
latest one failed, and the pull request page offers it as a button.

**Why:** the first real pull request through the system had its review fail
three times in two minutes because the model provider answered 503, and there
was then no way to try again except pushing another commit. Asking an author to
change their pull request to work around *our* provider being down is the wrong
way round.

A *new* job rather than a reset of the failed one: the failed row is the record
of what went wrong and when, and overwriting it would erase exactly the history
the pull request page exists to show.

The rule is deliberately narrow -- only when the **latest** attempt failed. A
queued or running one would be raced for the same commit; a succeeded one has a
review already, and a second would post twice; a cancelled one was superseded
by a newer commit that has its own job. Each is a `409` with a sentence saying
which.

**Cost:** one more way a job can be created, with no webhook behind it. The row
records that (`webhook_event_id` is null), so the audit trail still says who
started it.

**Found by:** the retry test for "only the latest attempt counts", which failed
because `created_at` is a *server default*. In SQLite that is second-granular
and hands back naive datetimes; in PostgreSQL `now()` is fixed for the whole
transaction, so two jobs created together tie exactly. The service now
normalises timezones before comparing, and the test sets an explicit timestamp
rather than racing a coarse clock.

## 113. One overloaded model falls back to a sibling

**Chosen:** on a 5xx from the primary Gemini model, the provider tries each
model in `DEVPILOT_LLM_FALLBACK_MODELS` in turn before counting the attempt as
failed. The default fallback is `gemini-3.5-flash`.

**Why:** retries with backoff assume the failure is *transient in time*. A
free-tier flash model answering "high demand, try again later" can stay that
way for many minutes, and on the first real pull request `gemini-3.6-flash` and
`gemini-3.7-flash` were both overloaded while `gemini-3.5-flash` answered in
fourteen seconds. Six attempts with backoff failed on schedule; the sibling
model was the retry that actually worked.

Strictly for server-side failures. A rate limit is per key, not per model, and
the key pool has already parked the key -- switching model would just spend the
next key's quota on the same problem. A refusal or a malformed response would
recur on any model. A rejected key is not a model problem at all. The test suite
caught the first implementation catching rate limits too, because
`LLMRateLimitError` subclasses `LLMTransientError`; that is the kind of
inheritance detail worth a test.

The model that actually answered is recorded on the review, not the one that
was asked. A fallback is visible in the data rather than blended into the
primary model's results, so "did reviews get worse after the upgrade?" stays
answerable.

**Cost:** a review can be produced by a model other than the configured one,
which is the point but also a thing to know. The log says when it happens.

## 114. A stored review can be posted after the fact

**Chosen:** `POST /reviews/{id}/publish` runs the publishing step for an
existing review, and the review page offers it while nothing has reached
GitHub yet.

**Why:** entry 70 decided that a failed post must not fail the review -- the
expensive work is done and stored -- and claimed that re-posting was a cheap
manual action. It was not; no such action existed. The first real pull request
found this: the App had read-only access to pull requests, the post got 403,
the review sat in the database with a correct, specific finding, and once the
permission was granted there was no way to send it. The retry endpoint
correctly refused, because the job had *succeeded*.

Idempotent, because a button can be clicked twice: a review already on GitHub is
left alone (`reviews.github_review_id` is the post-once marker, entry 68) and
findings already posted are not sent again. Synchronous, unlike the review
itself: this is one GitHub call on a person's click, and they want to know
whether it worked.

**Cost:** a network call inside a request handler, which the rest of the API
avoids. It is a single call of about a second, and the alternative -- queue it
and make the user poll -- is worse for exactly the person pressing the button.

**How it was found:** by claiming, in a message, that a retry would "just
repost" the stored review, and being told `409` by code I had written an hour
earlier. The code was right.

## 115. One dark palette, named by role

**Chosen:** the Tailwind theme defines colours by *role* -- `ink`, `surface`,
`raised`, `line`, `fg`, `muted`, `dim`, `accent` -- and pages use only those
names. There is one accent colour, amber, and the four severity colours.

**Alternative:** Tailwind's built-in shade scale (`slate-900`, `slate-500`, and
so on) used directly in every component, which is what the first version did.

**Why:** the first theme was twenty-six `text-slate-900`s and twenty-four
`text-slate-500`s scattered across nine files, and changing the look meant
touching every one. With roles, the whole theme is one object in the config,
and a page that says `text-muted` still means "secondary text" whatever colour
that turns out to be.

The single accent is deliberate. The obvious way to make an "AI product" look
is purple-to-blue gradients and glowing shapes; it has become the visual
equivalent of the phrase "unleash the power of", and it tells an interviewer
nothing about the work. Amber on near-black reads as an instrument panel --
something that measures -- which is what the product is.

**Cost:** the light theme is gone. Adding it back is a second colour object and
a class on `<html>`, but it was not asked for, and one theme done well beats two
done adequately.

## 116. The background is a canvas, not a video

**Chosen:** `Backdrop` draws a grid of points whose brightness follows a
slowly drifting noise field, with a faint radial sweep, at about a hundred lines
of arithmetic per frame.

**Alternative:** a looping video, which is what "a moving background" usually
means.

**Why:** a video is megabytes on first load, loops visibly, cannot react to the
viewport, and looks the same on every site that uses one. The canvas is a few
kilobytes of code, scales to any screen, is deterministic (seeded noise, so it
looks the same on every load), and respects `prefers-reduced-motion` by drawing
one static frame. It is also deliberately quiet -- low contrast, slow, mostly
grey with amber only at the peaks -- so it reads as texture behind the text
rather than competing with it. On the dashboard it runs at less than half
intensity, because there it sits behind data people read.

**Cost:** a `requestAnimationFrame` loop while the page is open. It is a few
thousand small `arc()` calls per frame, which is nothing on any hardware from
the last decade, but it is not free.

## 117. The landing page shows a real review happening

**Chosen:** the hero contains a scripted, looping animation of one review --
a diff, then findings appearing beside the lines they cite, then the risk
score filling in -- rather than a screenshot or an illustration.

**Why:** the product's claim is that findings are tied to lines and the score
is computed from them. Showing that happening is more persuasive than saying
it, and it is honest: the diff is a simplified version of a real pull request
this system reviewed, and the two findings are the ones it found.

Every sentence on the page is something the codebase does, usually with the
mechanism named. "Checks every claim against the diff" is a function. "Critical
10, high 7, medium 4, low 1" is the actual table. The numbers strip is real
counts. Marketing copy that an engineer can verify against the source is the
only kind that survives an interview.

**Cost:** the animation is hand-scripted, so it will drift from the product if
the pipeline changes shape. The stage list in it is short enough to keep in
step.

## 118. Model output is rendered with a forty-line renderer, not a Markdown library

**Chosen:** `ModelText` handles fenced code blocks and inline backticks, and
nothing else. Everything is emitted as React text nodes.

**Alternative:** a Markdown library, which would handle headings, lists, links
and tables too.

**Why:** the text being rendered is written by a model prompted with a pull
request that an arbitrary person authored. That is exactly the input an
attacker most wants to control, and a full Markdown renderer is a full parser's
worth of surface -- links to attacker-chosen URLs, images that beacon, and in
some libraries raw HTML. Code blocks and backticks are what a review model
actually produces; nothing else was appearing in real output, so nothing else
is rendered. React text nodes cannot become markup, whatever the string
contains.

**Cost:** a model that writes a bulleted list gets literal asterisks. That is a
legibility cost, not a correctness one, and it is visible rather than silent.


## 120. TLS is terminated inside the stack

**Chosen:** `docker-compose.prod.yml` includes a `caddy` service that owns ports
80 and 443, obtains a Let's Encrypt certificate for `DEVPILOT_DOMAIN`, and
routes `/api/*` and `/health*` to the API and everything else to the frontend.
No other service publishes a port.

**What entry 89 said:** bind the API and frontend to loopback and put a proxy
"in front" on the host. That was correct as far as it went, and it left the
hardest part of a deployment -- a certificate that renews -- as an exercise for
whoever did the deploying.

**Why:** the deployment target is a single free VM with nothing else on it.
"Install a proxy, get a certificate, set up renewal" is three more things to
get right on a machine that exists to run one `docker compose up`. Caddy does
all three with a hostname and nothing else, and the result is a stack that is
HTTPS end to end from one command.

Serving the API and the frontend from **one origin** has a second benefit
beyond convenience: the browser calls the host it loaded from, so no request
needs a CORS preflight, and cookies, CSP and the rate limiter have exactly one
origin to reason about. `VITE_API_BASE_URL` is now derived from the domain
rather than set separately, which removes a way for them to disagree.

**Cost:** a seventh container, and Caddy's certificate state lives in a volume
that must survive redeploys -- Let's Encrypt rate-limits re-issuance, so losing
it is more than an inconvenience. The `caddy-data` volume is named for that
reason. Locally the same Caddyfile runs with `DEVPILOT_DOMAIN=localhost`, where
Caddy issues from its internal CA and `curl -k` is enough to smoke-test the
routing.

## 121. The bootstrap script stops before starting the stack

**Chosen:** `deploy/bootstrap.sh` installs Docker, opens the firewall, clones
the repository and writes a `.env.prod` with a generated secret key and database
password -- then prints what is left and exits. It never runs `docker compose
up`.

**Why:** the first start needs real values that only the operator has: the
domain, the Gemini keys, the GitHub App identity. A script that started the
stack anyway would have to invent placeholders for them, and placeholders that
start a service are how they end up staying. The application already refuses
to boot in production on the placeholder secret key; the script honours the
same principle one step earlier by not manufacturing values it cannot know.

The two things it *does* generate -- the signing key and the database password
-- are the two that have no external counterpart and are worse if a human
chooses them.

**The firewall detail is the reason the script exists.** Oracle's Ubuntu images
ship iptables rules that drop everything except SSH, and they sit in front of
Docker's own rules. Opening the port in the cloud console is necessary but not
sufficient, and the symptom -- works from the VM, times out from everywhere
else -- points nowhere in particular. Encoding that in a script that anyone can
read is worth more than a paragraph in a document that nobody reads until after
they have hit it.

**Cost:** it is Ubuntu-specific and assumes `apt`. That is what the free tier
runs; supporting more would be speculative.

## 122. A single-container image for hosts that give you one service

**Chosen:** the root `Dockerfile` builds the frontend, installs the API,
and runs uvicorn and a solo-pool Celery worker side by side under one
entrypoint. The API serves the static bundle itself. `render.yaml` describes the
deployment.

**Why it exists:** the free tiers that ask for no card -- Render foremost --
give you one always-on web service, not a machine. The Compose stack needs
three processes and a proxy; the only way to run it there is to collapse it.
This is a compromise made on purpose and labelled as one: the Compose files
remain the reference, and this is what you run when a VM is not available.

Three things had to be true for the collapse to be safe:

* **The API's CSP would have blanked the page.** The security middleware sets
  `default-src 'none'` on every response, which is right for JSON and fatal for
  an HTML document. The document response sets a frontend policy of its own,
  and the middleware's `setdefault` leaves it alone. A test pins this, because
  it is the kind of thing that passes every API test and fails in a browser.

* **A catch-all route swallows 404s.** The SPA fallback must answer unknown
  paths with `index.html` so a refresh works -- but an unknown `/api/...` path
  must still be a JSON 404, not a 200 with a web page in it. The fallback
  refuses the API prefixes.

* **A dead worker must not hide behind a healthy API.** Two processes in one
  container means the platform's health check only sees one. The entrypoint
  waits on both and exits when either dies, so the failure becomes a restart
  rather than a week of silently unreviewed pull requests.

**Cost:** one restart unit, one memory budget, no independent scaling. All
acceptable at free-tier size and exactly the things to undo when it grows.

## 123. Plain `postgresql://` URLs are rewritten to the installed driver

**Chosen:** a validator on `database_url` turns `postgresql://` into
`postgresql+psycopg://`.

**Why:** hosted providers hand out the plain scheme, SQLAlchemy reads it as
"use psycopg2", psycopg2 is not installed, and the failure is an `ImportError`
from deep inside Alembic that mentions no URL at all. It surfaced the first time
the single-container image ran against a URL pasted from a dashboard. A
one-line rewrite means the string works as copied.

**Cost:** a URL that genuinely meant psycopg2 would be silently redirected.
Nothing here can mean that, because psycopg2 is not a dependency.

## 124. Startup migrations run under an advisory lock

**Chosen:** the single-container entrypoint runs migrations through
`scripts/migrate.py`, which takes a PostgreSQL advisory lock for the duration
of `alembic upgrade head`.

**Why:** entry 85 chose a one-shot migration job for the Compose stack precisely
because migrating on API startup races when more than one instance starts. The
single-container image then migrated on startup anyway -- it has no separate
job to run -- and the first Render deploy proved entry 85 right: the platform
started two instances of the new image at once, both ran the migration, and
the loser died on `relation "users" already exists`. The log showed
`applying migrations` twice, which is the whole diagnosis in three words.

An advisory lock is the smallest fix that keeps startup migrations. The second
instance blocks until the first releases the lock, then runs Alembic itself and
finds nothing to do. Verified by racing two migrations against a fresh
database: both exited 0, seven migrations ran once, one consistent schema.

**Cost:** a second database connection held open for the duration of the
migration, and a startup that is slower by however long the *other* instance's
migration takes. Neither matters at this size.

**The lesson worth keeping:** a tradeoff recorded for one deployment shape does
not automatically transfer to the next. Entry 85 was known; it still had to be
rediscovered because the new entrypoint was written without re-reading it.

## 125. Connect timeouts are a property of the deployment, not the code

**Chosen:** `render.yaml` sets the database connect timeout to 15 seconds; the
code default stays at 3.

**Why:** three seconds is right when PostgreSQL is a container on the same
Docker network -- it turns "the database is down" into a fast 503 instead of a
hung request, which is why entry 3 chose it. Against a serverless database it
is wrong in a way that hides itself: Neon suspends an idle compute and takes a
few seconds to wake, the first connection times out before the wake completes,
the compute sees no completed connection and suspends again, and readiness
reports `OperationalError` on every probe with a latency of exactly the timeout.
Migrations at startup succeeded, because Alembic sets no timeout, which made
the failure look like something other than what it was.

Raising the default in code would give the Compose deployment a worse failure
mode to protect a deployment it is not running. The value belongs beside the
database it is tuned for.

**Cost:** a readiness probe against a genuinely dead database now takes up to
15 seconds to say so on Render. The liveness probe, which the platform actually
watches, touches no dependency and is unaffected.

