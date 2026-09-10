# Connecting DevPilot to a real language model

DevPilot ships in **mock mode**, which needs no API key and costs nothing. The
whole pipeline runs: the diff is fetched and parsed, static analysis runs, a
review is produced, validated, scored and stored, and the dashboard shows it.

What mock mode does *not* do is think about your code. It restates what the
static analyser already found and says so in every summary:

> `[Mock review - no language model was called.]`

Its findings are pinned at confidence 0.5, deliberately below the posting
threshold, so a mocked finding can never be posted to a real pull request.

This document is for when you want real reviews.

- [Choosing a provider](#choosing-a-provider)
- [Gemini — the free option](#gemini--the-free-option)
- [Anthropic — the paid option](#anthropic--the-paid-option)
- [Step 3 — verify](#step-3--verify)
- [What a review costs](#what-a-review-costs)
- [Controlling spend](#controlling-spend)
- [Troubleshooting](#troubleshooting)
- [Security notes](#security-notes)

---

## Choosing a provider

`DEVPILOT_LLM_MODE` names the provider. There is no separate "live" switch,
because "which vendor" and "real or mocked" are one decision — two settings
could contradict each other.

| Mode | Cost | Notes |
| --- | --- | --- |
| `mock` | Free | Default. No key, no network, deterministic. |
| `gemini` | **Free tier** | Google Gemini. Good enough for real reviews at no cost. |
| `anthropic` | Paid per review | Claude. The strongest reviews here. |

Both real providers implement the same `LLMProvider` Protocol, so **nothing else
in the pipeline changes**: the same prompt, the same Pydantic schema, the same
validation pass that discards findings the diff does not support, and the same
deterministic risk score. Switching vendors is one environment variable.

Whichever you pick, the calls are outbound only — no registration flow, no
webhook, no tunnel.

---

## Gemini — the free option

### Step 1 — get one or more keys

1. Sign in at <https://aistudio.google.com>
2. **Get API key → Create API key**
3. Copy it. Repeat if you want more than one.

Free-tier limits are applied **per key**, so DevPilot rotates across however
many you give it. One key is fine; more just means more headroom before you hit
a per-minute limit.

> Use only keys you legitimately hold. Creating extra Google accounts to
> multiply free quota is against Google's terms — that is a different thing from
> rotating keys you already own.

### Step 2 — configure DevPilot

In `.env` (git-ignored — never commit this):

```dotenv
DEVPILOT_LLM_MODE=gemini
DEVPILOT_GEMINI_API_KEYS=AIza-first-key,AIza-second-key,AIza-third-key
```

Comma-separated, no quotes, no spaces needed. Blank entries and duplicates are
ignored, so a trailing comma is harmless.

Leave `DEVPILOT_LLM_MODEL` blank and it defaults to `gemini-3.6-flash` — flash
rather than pro because the free tier allows far more requests per day of it, and
pinned to a version rather than the floating `gemini-flash-latest` alias because
`reviews.model_name` is stored so results stay comparable.

> `gemini-2.5-flash` is closed to new accounts and answers `404`. If you see
> that, you are on an older model id.

**On structured output.** The Anthropic path constrains generation to the
`LLMReview` schema. Gemini rejects this schema in its `response_schema` field —
see tradeoff 100 — so DevPilot sends the schema *in the prompt* and relies on
Pydantic validation plus `DEVPILOT_LLM_MAX_VALIDATION_RETRIES` instead. Reviews
are validated identically either way; Gemini just needs one more round trip
occasionally.

### How the rotation behaves

| Situation | What happens |
| --- | --- |
| Normal request | Next key in the rotation, round-robin |
| Key returns **429** | Parked until Gemini's own `retryDelay` elapses, then rejoins |
| Key returns **401/403** | Retired permanently — a revoked key will never start working |
| Every key unavailable | Reported as a rate limit, so the worker backs off and retries |

Keys are **never written to logs**. Log lines identify a key by its position
(`key-2`), so you can see which one misbehaved without the secret reaching a log
aggregator.

One honest limitation: the rotation state lives in one process. Celery's prefork
workers are separate processes, so four workers hold four independent views and
spread load approximately rather than exactly. The failure mode is benign — a
second process tries a parked key, gets a 429, and parks it too.

---

## Anthropic — the paid option

### Step 1 — get an API key

1. Sign in at <https://console.anthropic.com>
2. Add a payment method and, ideally, a **spend limit** — this is the safety net
   that matters most while you are still testing
3. **API keys → Create key**, and copy it (it is shown once)

The key starts with `sk-ant-`.

### Step 2 — configure DevPilot

```dotenv
DEVPILOT_LLM_MODE=anthropic
DEVPILOT_ANTHROPIC_API_KEY=sk-ant-your-key-here
```

**This one costs money.** Every review is a paid API call. Read
[What a review costs](#what-a-review-costs) first.

---

## Restarting

Either way, restart the worker — it is the process that calls the model:

```bash
docker compose restart worker
```

The API server never calls the model, so it does not need the key. If you deploy
the two separately, only the worker needs it.

## Step 3 — verify

Trigger a review (open a pull request, or replay a webhook), then look at what
was stored:

```bash
docker compose exec postgres psql -U devpilot -d devpilot \
  -c "select risk_score, model_name, left(summary, 80) from reviews order by created_at desc limit 1"
```

Two things tell you it worked:

- `model_name` is the real model (`gemini-3.6-flash`, `claude-opus-5`), not
  `mock-reviewer`
- the summary does **not** begin with `[Mock review …]`

The worker logs the same transition:

```bash
docker compose logs -f worker | grep pipeline.review_completed
```

## What a review costs

A review sends the diff, the changed line numbers and the static-analysis output,
and receives a structured review back. For a typical pull request that is a few
thousand input tokens and a few hundred output tokens.

At Claude Opus 5 pricing ($5 per million input tokens, $25 per million output),
a small review costs on the order of a few cents. A large one costs more, roughly
in proportion to the diff.

Two things bound it structurally, both already in place:

- **Diffs above `DEVPILOT_MAX_DIFF_BYTES` are refused outright.** A sprawling
  change produces a prompt no model reads carefully anyway.
- **A new push cancels the queued review for the superseded commit**, so an
  actively developed pull request is not reviewed once per push-in-flight.

The actual token counts for every review are stored on the `reviews` row
(`prompt_tokens`, `completion_tokens`), so real spend can be measured rather than
estimated.

## Controlling spend

| Lever | Effect |
| --- | --- |
| `DEVPILOT_LLM_MODE=gemini` | Free tier instead of paid. The biggest lever. |
| A **spend limit** in the Anthropic Console | The only hard stop. Set one. |
| `DEVPILOT_LLM_EFFORT=medium` | Less reasoning per review, lower cost |
| `DEVPILOT_LLM_MODEL=claude-sonnet-5` | Cheaper per token than Opus |
| `DEVPILOT_MAX_DIFF_BYTES` | Refuse large diffs sooner |
| `DEVPILOT_LLM_MODE=mock` | Back to free, instantly |

Effort is the first lever worth trying: reviewing code rewards reasoning depth,
which is why the default is `high`, but `medium` is often enough for routine
changes and costs meaningfully less.

## Troubleshooting

**Reviews still say `[Mock review …]`.** `DEVPILOT_LLM_MODE` is still `mock`, or
the worker was not restarted. The mode is read at startup.

**Job fails with `LLMConfigurationError`.** A real provider selected with no key
set. The error message names the variable.

**Job fails with `LLMAuthenticationError`.** The key was rejected — wrong value,
revoked, or no credit on the account. Retrying will not help, which is why it is
classified permanent.

**Job retries with `LLMRateLimitError`.** Expected under load. The worker waits
exactly as long as the API asked before trying again.

**Job fails with `LLMInvalidResponseError` after several attempts.** The model
returned something that did not satisfy the schema three times running. Usually
the prompt exceeded the context window — check the diff size.

**Reviews come back with no findings.** That is a legitimate answer, and the
prompt explicitly encourages it over invented nitpicks. Check the worker log for
`llm.findings_rejected` first: findings citing files or lines outside the diff
are discarded before they reach the database.

## Security notes

**The key is a billing credential.** Anyone holding it can spend your money.
`.gitignore` already excludes `.env`; keep it out of logs and screenshots too.

**Pull request code is sent to the model.** For a private repository, that means
its source leaves your infrastructure. This is inherent to the design, not an
oversight, but it is a decision to make deliberately before pointing DevPilot at
anything sensitive.

**The model's output is never trusted.** It is constrained to a schema at
request time, validated by Pydantic on arrival, and then checked against the
actual diff — any finding citing a file or line the pull request did not touch
is discarded. The risk score is computed in Python from the surviving findings;
the model is never asked for it.

**Findings below `DEVPILOT_LLM_MIN_CONFIDENCE_TO_POST` are stored but never
posted.** A reviewer that posts its own guesses is one people stop reading.
