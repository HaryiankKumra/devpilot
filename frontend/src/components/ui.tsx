import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

/** Shared primitives, kept small: this project is a review tool, not a design system. */

export function PageHeader({
  title,
  description,
  action,
  eyebrow,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  eyebrow?: string;
}) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4 animate-rise">
      <div>
        {eyebrow && <p className="eyebrow mb-2">{eyebrow}</p>}
        <h1 className="text-2xl font-semibold tracking-tight text-fg">{title}</h1>
        {description && (
          <p className="mt-1.5 max-w-2xl text-sm text-muted">{description}</p>
        )}
      </div>
      {action}
    </div>
  );
}

export function Card({
  children,
  className = '',
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-line bg-surface/80 shadow-card backdrop-blur ${className}`}
    >
      {children}
    </div>
  );
}

/**
 * Empty states say what to do next rather than only that there is nothing here.
 * "No reviews yet" alone leaves a new user stuck on their first screen.
 */
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-dashed border-line bg-surface/50 p-12 text-center">
      <p className="text-sm font-medium text-fg">{title}</p>
      <p className="mx-auto mt-1.5 max-w-md text-sm text-muted">{description}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-line bg-surface/60 p-12 text-center">
      {/* A thin sweep instead of a spinner: cheaper, and it matches the rest. */}
      <div className="absolute inset-x-0 top-0 h-px overflow-hidden">
        <div className="h-full w-1/3 animate-sweep bg-gradient-to-r from-transparent via-accent to-transparent" />
      </div>
      <p className="font-mono text-xs text-muted" role="status">
        {label}
      </p>
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      className="rounded-xl border border-severity-critical/40 bg-severity-critical/10 p-5"
      role="alert"
    >
      <p className="text-sm font-medium text-fg">Something went wrong</p>
      <p className="mt-1 font-mono text-xs text-muted">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-md border border-line bg-raised px-3 py-1.5 text-sm font-medium text-fg transition hover:border-muted"
        >
          Try again
        </button>
      )}
    </div>
  );
}

export function Button({
  children,
  onClick,
  disabled,
  variant = 'primary',
  type = 'button',
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: 'primary' | 'secondary';
  type?: 'button' | 'submit';
}) {
  const styles =
    variant === 'primary'
      ? 'bg-accent text-accent-ink hover:bg-accent-hover shadow-glow'
      : 'border border-line bg-raised text-fg hover:border-muted';

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md px-3.5 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles}`}
    >
      {children}
    </button>
  );
}

export function CardLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="block rounded-xl border border-line bg-surface/80 p-4 shadow-card backdrop-blur transition hover:border-muted hover:bg-raised"
    >
      {children}
    </Link>
  );
}

/** Short commit SHAs, the way every Git tool shows them. */
export function ShortSha({ sha }: { sha: string }) {
  return (
    <code className="rounded bg-raised px-1.5 py-0.5 font-mono text-xs text-muted">
      {sha.slice(0, 7)}
    </code>
  );
}

/**
 * Absolute time in the tooltip, relative in the text.
 * "3 hours ago" is what a person wants; the exact timestamp is what they need
 * when something looks wrong.
 */
export function RelativeTime({ iso }: { iso: string }) {
  const date = new Date(iso);
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);

  const label =
    seconds < 60
      ? 'just now'
      : seconds < 3600
        ? `${Math.floor(seconds / 60)}m ago`
        : seconds < 86400
          ? `${Math.floor(seconds / 3600)}h ago`
          : `${Math.floor(seconds / 86400)}d ago`;

  return (
    <time
      dateTime={iso}
      title={date.toLocaleString()}
      className="font-mono text-xs text-dim"
    >
      {label}
    </time>
  );
}
