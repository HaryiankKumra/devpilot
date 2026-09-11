import { useApiStatus } from '@/features/health/useApiStatus';

/** Small indicator showing whether the API is reachable. */
export function ApiStatusBadge() {
  const { data, isPending, isError } = useApiStatus();

  const { label, className } = isPending
    ? { label: 'Checking API…', className: 'bg-raised text-muted' }
    : isError
      ? {
          label: 'API unreachable',
          className: 'bg-severity-critical/15 text-severity-critical',
        }
      : {
          label: `API ${data.version} · ${data.environment}`,
          className: 'bg-emerald-400/10 text-emerald-300',
        };

  return (
    <span
      className={`rounded-full px-2.5 py-1 font-mono text-[11px] font-medium ${className}`}
      // Announce changes politely so the badge is useful to screen readers.
      role="status"
      aria-live="polite"
    >
      {label}
    </span>
  );
}
