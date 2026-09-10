import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

/** Shared primitives, kept small: this project is a review tool, not a design system. */

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {description && <p className="mt-1 text-sm text-slate-600">{description}</p>}
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
    <div className={`rounded-lg border border-slate-200 bg-white ${className}`}>
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
    <div className="rounded-lg border border-dashed border-slate-300 bg-white p-10 text-center">
      <p className="text-sm font-medium text-slate-900">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-sm text-slate-600">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-10 text-center">
      <p className="text-sm text-slate-500" role="status">
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
    <div className="rounded-lg border border-red-200 bg-red-50 p-6" role="alert">
      <p className="text-sm font-medium text-red-900">Something went wrong</p>
      <p className="mt-1 text-sm text-red-800">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-900 hover:bg-red-100"
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
      ? 'bg-slate-900 text-white hover:bg-slate-800'
      : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50';

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md px-3 py-1.5 text-sm font-medium transition disabled:opacity-60 ${styles}`}
    >
      {children}
    </button>
  );
}

export function CardLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="block rounded-lg border border-slate-200 bg-white p-4 transition hover:border-slate-300 hover:bg-slate-50"
    >
      {children}
    </Link>
  );
}

/** Short commit SHAs, the way every Git tool shows them. */
export function ShortSha({ sha }: { sha: string }) {
  return (
    <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-700">
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
    <time dateTime={iso} title={date.toLocaleString()} className="text-xs text-slate-500">
      {label}
    </time>
  );
}
