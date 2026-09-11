import { Backdrop } from '@/components/Backdrop';
import { Wordmark } from '@/components/Wordmark';
import { useAuth } from '@/features/auth/useAuth';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

/**
 * The public front page. Explains what DevPilot does, how a review actually
 * happens, and why the output can be trusted -- in that order, because the
 * third question is the one an engineer asks first.
 *
 * Copy is deliberately concrete. Every claim on this page is something the
 * codebase does, with the number or the mechanism beside it.
 */
export function LandingPage() {
  const { user } = useAuth();
  const primaryTo = user ? '/dashboard' : '/register';
  const primaryLabel = user ? 'Open dashboard' : 'Get started';

  return (
    <div className="min-h-screen">
      <Backdrop intensity={1.15} />

      <header className="sticky top-0 z-20 border-b border-line/60 bg-ink/70 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-3.5">
          <Wordmark />
          <nav className="flex items-center gap-2" aria-label="Site">
            <a
              href="#how"
              className="hidden px-3 py-1.5 text-sm text-muted hover:text-fg sm:inline"
            >
              How it works
            </a>
            <a
              href="#why"
              className="hidden px-3 py-1.5 text-sm text-muted hover:text-fg sm:inline"
            >
              Why trust it
            </a>
            {!user && (
              <Link to="/login" className="px-3 py-1.5 text-sm text-muted hover:text-fg">
                Sign in
              </Link>
            )}
            <Link
              to={primaryTo}
              className="rounded-md bg-accent px-3.5 py-1.5 text-sm font-medium text-accent-ink shadow-glow transition hover:bg-accent-hover"
            >
              {primaryLabel}
            </Link>
          </nav>
        </div>
      </header>

      <main>
        {/* ---------------------------------------------------------- hero */}
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-5 pb-20 pt-20 lg:grid-cols-[1.05fr_1fr] lg:pt-28">
          <div className="animate-rise">
            <p className="eyebrow">Pull request review · GitHub App</p>
            <h1 className="mt-4 text-4xl font-semibold leading-[1.08] tracking-tight text-fg sm:text-5xl lg:text-[3.4rem]">
              Code review that
              <br />
              shows its work.
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted">
              DevPilot listens for pull requests, runs static analysis, reads the
              surrounding repository, and asks a language model for a structured review —
              then checks every claim against the diff before it posts a word.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link
                to={primaryTo}
                className="rounded-md bg-accent px-5 py-2.5 text-sm font-medium text-accent-ink shadow-glow transition hover:bg-accent-hover"
              >
                {primaryLabel}
              </Link>
              <a
                href="#how"
                className="rounded-md border border-line bg-raised/60 px-5 py-2.5 text-sm font-medium text-fg transition hover:border-muted"
              >
                See how a review happens
              </a>
            </div>
            <p className="mt-6 font-mono text-xs text-dim">
              Runs free on Gemini · Never blocks a merge · Every finding cites a line
            </p>
          </div>

          <LiveReview />
        </section>

        <div className="hairline mx-auto max-w-6xl" />

        {/* -------------------------------------------------- what it does */}
        <section className="mx-auto max-w-6xl px-5 py-20">
          <p className="eyebrow">What it does</p>
          <h2 className="mt-3 max-w-2xl text-3xl font-semibold tracking-tight text-fg">
            The first pass of review, done before anyone opens the tab.
          </h2>
          <div className="mt-12 grid gap-5 md:grid-cols-3">
            <Feature
              index="01"
              title="Listens"
              body="Install the GitHub App on a repository. Every pull request event arrives as a signed webhook, is verified against the secret, and is recorded before anything else happens — so a redelivery does no duplicate work."
            />
            <Feature
              index="02"
              title="Reviews"
              body="A worker fetches the diff, runs a parser-based static analyser, pulls in the existing code the change touches from a vector index, and asks the model for findings in a strict schema — with file, line, severity and confidence."
            />
            <Feature
              index="03"
              title="Reports"
              body="Findings that survive validation go back to the pull request as one review with inline comments. Everything — including the ones held back — lands on a dashboard with the risk score and the tokens it cost."
            />
          </div>
        </section>

        {/* ------------------------------------------------ how it works */}
        <section id="how" className="border-y border-line/60 bg-surface/40 py-20">
          <div className="mx-auto max-w-6xl px-5">
            <p className="eyebrow">How a review happens</p>
            <h2 className="mt-3 max-w-2xl text-3xl font-semibold tracking-tight text-fg">
              Eight stages. Each one is a function you can read.
            </h2>
            <p className="mt-4 max-w-2xl text-muted">
              The API stays thin — it verifies, records and queues. Everything slow or
              failure-prone runs on a worker, where a retry is cheap and a failure is
              stored with enough context to explain itself.
            </p>
            <Pipeline />
          </div>
        </section>

        {/* -------------------------------------------------- why trust it */}
        <section id="why" className="mx-auto max-w-6xl px-5 py-20">
          <p className="eyebrow">Why you'd trust it</p>
          <h2 className="mt-3 max-w-2xl text-3xl font-semibold tracking-tight text-fg">
            The model is a source, not an authority.
          </h2>
          <div className="mt-12 grid gap-x-12 gap-y-10 md:grid-cols-2">
            <Reason
              title="Every claim is checked against the diff"
              body="A finding that names a file not in the pull request, or a line the change did not touch, is discarded before it is stored. The model can be wrong; it cannot be wrong on your pull request."
            />
            <Reason
              title="The risk score is arithmetic, not opinion"
              body="Critical 10, high 7, medium 4, low 1 — summed, scaled, floored by the worst severity present, capped at 100. Computed in Python from validated findings. The same findings always produce the same number."
              code="max(sum(weights) × 5, floor) → 0–100"
            />
            <Reason
              title="It comments; it never blocks"
              body="Reviews post as COMMENT, never REQUEST_CHANGES. An automated reviewer that can block a merge on a hallucination gets uninstalled the first time it does. Advisory output earns its place."
            />
            <Reason
              title="Low confidence stays off your pull request"
              body="Findings below the confidence threshold are kept on the dashboard and never posted. A weak finding costs nothing in a list you chose to open, and a lot in front of your colleagues."
            />
            <Reason
              title="Failures are records, not mysteries"
              body="A job that fails stores the error type, message and traceback. The pull request page shows every attempt — because 'why has this not been reviewed?' is the question that page exists to answer."
            />
            <Reason
              title="It costs nothing to run"
              body="Mock mode runs the whole pipeline with no keys. Gemini's free tier runs real reviews; several keys rotate, a throttled key is parked until its window resets, and an overloaded model falls back to a sibling."
            />
          </div>
        </section>

        {/* ----------------------------------------------------- numbers */}
        <section className="border-y border-line/60 bg-surface/40 py-14">
          <div className="mx-auto grid max-w-6xl gap-8 px-5 sm:grid-cols-2 lg:grid-cols-4">
            <Stat value="583" label="tests, run against real Postgres and Redis in CI" />
            <Stat
              value="115"
              label="documented engineering decisions, each with its cost"
            />
            <Stat value="0" label="findings posted without a file and line" />
            <Stat value="22" label="API endpoints, every one scoped to the caller" />
          </div>
        </section>

        {/* --------------------------------------------------------- CTA */}
        <section className="mx-auto max-w-6xl px-5 py-24 text-center">
          <h2 className="text-3xl font-semibold tracking-tight text-fg">
            Install it on one repository.
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-muted">
            Register, connect GitHub, pick a repository. The next pull request gets
            reviewed. No card, no keys of ours to buy.
          </p>
          <Link
            to={primaryTo}
            className="mt-8 inline-block rounded-md bg-accent px-6 py-3 text-sm font-medium text-accent-ink shadow-glow transition hover:bg-accent-hover"
          >
            {primaryLabel}
          </Link>
        </section>
      </main>

      <footer className="border-t border-line/60">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-5 py-6">
          <Wordmark />
          <p className="font-mono text-xs text-dim">
            FastAPI · Celery · PostgreSQL + pgvector · React · Gemini
          </p>
        </div>
      </footer>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Live review: a looping, scripted animation of one review happening.        */
/* -------------------------------------------------------------------------- */

const DIFF_LINES: { n: number; kind: '+' | '-' | ' '; text: string }[] = [
  { n: 112, kind: ' ', text: 'const onFileChange = (event) => {' },
  { n: 113, kind: '-', text: '  const files = validate(event.target.files);' },
  { n: 113, kind: '+', text: '  const files = event.target.files;' },
  { n: 114, kind: ' ', text: '  if (!files) return;' },
  { n: 115, kind: '-', text: '  setImages(Array.from(files));' },
  { n: 116, kind: ' ', text: '};' },
];

const FINDINGS = [
  {
    severity: 'high',
    line: 115,
    title: 'setImages is no longer called',
    detail: 'Selected files are never stored, so submission sends none.',
    confidence: 0.95,
  },
  {
    severity: 'medium',
    line: 113,
    title: 'File validation removed',
    detail: 'Type and size checks no longer run before upload.',
    confidence: 0.82,
  },
] as const;

const STAGES = [
  'webhook verified',
  'diff fetched · 1 file · 2 lines',
  'static analysis · 0 findings',
  'retrieved 3 related chunks',
  'model review · validated',
  'risk 50 · posted 2 comments',
];

function LiveReview() {
  // One step every ~1.4s, looping. Step 0 shows the diff; findings and the
  // score arrive on later steps; the last step holds before restarting.
  const [step, setStep] = useState(0);
  useEffect(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce) {
      setStep(STAGES.length - 1);
      return;
    }
    const id = window.setInterval(
      () => setStep((s) => (s + 1) % (STAGES.length + 1)),
      1400,
    );
    return () => window.clearInterval(id);
  }, []);

  const shown = Math.min(step, STAGES.length - 1);
  const findingsVisible = shown >= 4 ? FINDINGS.length : 0;
  const score = shown >= 5 ? 50 : 0;

  return (
    <div className="animate-rise rounded-xl border border-line bg-surface/80 shadow-card backdrop-blur [animation-delay:120ms]">
      {/* title bar */}
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <div className="flex items-center gap-2 font-mono text-xs text-muted">
          <span className="text-dim">cityserve</span>
          <span className="text-dim">/</span>
          <span>pull #3</span>
        </div>
        <div className="flex items-center gap-2 font-mono text-[11px]">
          <span
            className={`h-1.5 w-1.5 rounded-full ${shown >= 5 ? 'bg-emerald-400' : 'bg-accent animate-blink'}`}
          />
          <span className="text-muted">{STAGES[shown]}</span>
        </div>
      </div>

      {/* diff */}
      <div className="px-4 py-3 font-mono text-[12.5px] leading-6">
        <div className="mb-1 text-dim">src/pages/ComplaintForm.tsx</div>
        {DIFF_LINES.map((line, i) => {
          const tone =
            line.kind === '+'
              ? 'bg-emerald-400/10 text-emerald-200'
              : line.kind === '-'
                ? 'bg-severity-critical/10 text-red-200'
                : 'text-muted';
          const flagged =
            findingsVisible > 0 &&
            FINDINGS.some((f) => f.line === line.n && line.kind !== ' ');
          return (
            <div
              key={i}
              className={`-mx-2 flex gap-3 rounded px-2 transition-colors ${tone} ${flagged ? 'ring-1 ring-accent/50' : ''}`}
            >
              <span className="w-8 select-none text-right text-dim">{line.n}</span>
              <span className="w-3 select-none text-dim">{line.kind}</span>
              <span className="whitespace-pre">{line.text}</span>
            </div>
          );
        })}
      </div>

      {/* findings */}
      <div className="border-t border-line px-4 py-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="eyebrow">Findings</span>
          <RiskMeter score={score} />
        </div>
        <div className="space-y-2">
          {FINDINGS.slice(0, findingsVisible).map((f) => (
            <div
              key={f.line}
              className="animate-rise flex items-start gap-3 rounded-md border border-line bg-raised/70 px-3 py-2"
            >
              <span
                className={`mt-0.5 rounded-full px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase ${
                  f.severity === 'high'
                    ? 'bg-severity-high/15 text-severity-high'
                    : 'bg-severity-medium/15 text-severity-medium'
                }`}
              >
                {f.severity}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm text-fg">{f.title}</span>
                  <span className="font-mono text-[11px] text-dim">
                    L{f.line} · {f.confidence.toFixed(2)}
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-muted">{f.detail}</p>
              </div>
            </div>
          ))}
          {findingsVisible === 0 && (
            <div className="rounded-md border border-dashed border-line px-3 py-4 text-center font-mono text-xs text-dim">
              {shown < 4 ? 'reading…' : ''}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function RiskMeter({ score }: { score: number }) {
  const tone =
    score >= 75
      ? 'text-severity-critical'
      : score >= 50
        ? 'text-severity-high'
        : score >= 25
          ? 'text-severity-medium'
          : score > 0
            ? 'text-severity-low'
            : 'text-dim';
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-raised">
        <div
          className="h-full rounded-full bg-current transition-[width] duration-700 ease-out"
          style={{ width: `${score}%` }}
          // Colour comes from the parent's `text-*` via currentColor.
        />
      </div>
      <span className={`w-14 font-mono text-xs tabular-nums ${tone}`}>
        risk {String(score).padStart(2, '0')}
      </span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */

function Feature({ index, title, body }: { index: string; title: string; body: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface/70 p-6 shadow-card backdrop-blur">
      <div className="font-mono text-xs text-accent">{index}</div>
      <h3 className="mt-3 text-lg font-semibold text-fg">{title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-muted">{body}</p>
    </div>
  );
}

const PIPELINE = [
  ['Verify', 'HMAC-SHA256 over raw bytes, before parsing'],
  ['Record', 'Delivery id is the idempotency key'],
  ['Queue', 'Dispatched only after the commit'],
  ['Fetch', 'Diff and changed lines from GitHub'],
  ['Analyse', 'Parser-based static analysis, never executes code'],
  ['Retrieve', 'Related code from pgvector, labelled "not under review"'],
  ['Review', 'Structured output, validated line by line'],
  ['Score & post', 'Deterministic risk, one review, inline comments'],
] as const;

function Pipeline() {
  const [active, setActive] = useState(0);
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const id = window.setInterval(
      () => setActive((a) => (a + 1) % PIPELINE.length),
      1600,
    );
    return () => window.clearInterval(id);
  }, []);

  return (
    <ol className="mt-12 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {PIPELINE.map(([name, detail], i) => {
        const on = i === active;
        const done = i < active;
        return (
          <li
            key={name}
            className={`rounded-lg border p-4 transition-colors duration-500 ${
              on
                ? 'border-accent/60 bg-accent-dim'
                : done
                  ? 'border-line bg-surface/60'
                  : 'border-line/60 bg-surface/30'
            }`}
          >
            <div className="flex items-center gap-2 font-mono text-[11px]">
              <span
                className={on ? 'text-accent' : done ? 'text-emerald-400' : 'text-dim'}
              >
                {done ? '✓' : String(i + 1).padStart(2, '0')}
              </span>
              <span className={on ? 'text-fg' : 'text-muted'}>{name}</span>
            </div>
            <p
              className={`mt-2 text-xs leading-relaxed ${on ? 'text-fg/80' : 'text-dim'}`}
            >
              {detail}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

function Reason({ title, body, code }: { title: string; body: string; code?: string }) {
  return (
    <div>
      <h3 className="text-base font-semibold text-fg">{title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-muted">{body}</p>
      {code && (
        <code className="mt-3 inline-block rounded bg-raised px-2 py-1 font-mono text-xs text-accent">
          {code}
        </code>
      )}
    </div>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <div className="font-mono text-4xl font-semibold tabular-nums text-fg">{value}</div>
      <p className="mt-2 text-sm text-muted">{label}</p>
    </div>
  );
}
