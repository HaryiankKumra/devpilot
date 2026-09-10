import { riskBand } from '@/components/review/riskBands';
import type { Severity } from '@/features/reviews/api';

export function RiskScore({ score, size = 'md' }: { score: number; size?: 'sm' | 'md' }) {
  const band = riskBand(score);
  const dimensions = size === 'sm' ? 'h-10 w-10 text-sm' : 'h-14 w-14 text-lg';

  return (
    <div className="flex items-center gap-3">
      <div
        className={`flex ${dimensions} shrink-0 items-center justify-center rounded-full font-semibold ring-1 ${band.classes}`}
        // The number alone is meaningless to a screen reader; the band is the
        // part a person actually acts on.
        aria-label={`Risk score ${score} out of 100, ${band.label}`}
      >
        {score}
      </div>
      {size === 'md' && (
        <div className="leading-tight">
          <div className="text-sm font-medium text-slate-900">{band.label} risk</div>
          <div className="text-xs text-slate-500">{score}/100</div>
        </div>
      )}
    </div>
  );
}

const SEVERITY_STYLES: Record<Severity, string> = {
  critical: 'bg-red-100 text-red-900 ring-red-300',
  high: 'bg-orange-100 text-orange-900 ring-orange-300',
  medium: 'bg-amber-100 text-amber-900 ring-amber-300',
  low: 'bg-sky-100 text-sky-900 ring-sky-300',
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
    <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium capitalize text-slate-700 ring-1 ring-slate-300">
      {category}
    </span>
  );
}
