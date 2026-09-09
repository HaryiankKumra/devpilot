import { useApiStatus } from '@/features/health/useApiStatus';

/** Small indicator showing whether the API is reachable. */
export function ApiStatusBadge() {
  const { data, isPending, isError } = useApiStatus();

  const { label, className } = isPending
    ? { label: 'Checking API…', className: 'bg-slate-100 text-slate-600' }
    : isError
      ? { label: 'API unreachable', className: 'bg-red-100 text-red-800' }
      : {
          label: `API ${data.version} · ${data.environment}`,
          className: 'bg-emerald-100 text-emerald-800',
        };

  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs font-medium ${className}`}
      // Announce changes politely so the badge is useful to screen readers.
      role="status"
      aria-live="polite"
    >
      {label}
    </span>
  );
}
