import { riskBand } from '@/components/review/riskBands';
import type { Severity } from '@/features/reviews/api';

export function RiskScore({ score, size = 'md' }: { score: number; size?: 'sm' | 'md' }) {
  const band = riskBand(score);
  const dimensions = size === 'sm' ? 'h-10 w-10 text-sm' : 'h-14 w-14 text-lg';

  return (
    <div className="flex items-center gap-3">
      <div
        className={`flex ${dimensions} shrink-0 items-center justify-center rounded-full font-mono font-semibold tabular-nums ring-1 ${band.classes}`}
        // The number alone is meaningless to a screen reader; the band is the
        // part a person actually acts on.
        aria-label={`Risk score ${score} out of 100, ${band.label}`}
      >
        {score}
      </div>
      {size === 'md' && (
        <div className="leading-tight">
          <div className="text-sm font-medium text-fg">{band.label} risk</div>
          <div className="font-mono text-xs text-dim">{score}/100</div>
        </div>
      )}
    </div>
  );
}

const SEVERITY_STYLES: Record<Severity, string> = {
  critical: 'bg-severity-critical/15 text-severity-critical ring-severity-critical/50',
  high: 'bg-severity-high/15 text-severity-high ring-severity-high/50',
  medium: 'bg-severity-medium/15 text-severity-medium ring-severity-medium/50',
  low: 'bg-severity-low/15 text-severity-low ring-severity-low/50',
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium capitalize ring-1 ${SEVERITY_STYLES[severity]}`}
    >
      {severity}
    </span>
  );
}

export function CategoryBadge({ category }: { category: string }) {
  return (
    <span className="inline-flex items-center rounded-full bg-raised px-2 py-0.5 text-xs font-medium capitalize text-muted ring-1 ring-line">
      {category}
    </span>
  );
}
