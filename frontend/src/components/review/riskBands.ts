/**
 * Risk score bands.
 *
 * These match `SEVERITY_FLOORS` in the backend's `risk.py` exactly. If the two
 * ever disagree, a review containing a critical finding could be scored 75 by
 * the backend and drawn as "medium" here — the number and the colour would
 * contradict each other, which is worse than showing neither.
 */
const BANDS = [
  {
    min: 75,
    label: 'Critical',
    classes: 'bg-severity-critical/15 text-severity-critical ring-severity-critical/50',
  },
  {
    min: 50,
    label: 'High',
    classes: 'bg-severity-high/15 text-severity-high ring-severity-high/50',
  },
  {
    min: 25,
    label: 'Medium',
    classes: 'bg-severity-medium/15 text-severity-medium ring-severity-medium/50',
  },
  {
    min: 1,
    label: 'Low',
    classes: 'bg-severity-low/15 text-severity-low ring-severity-low/50',
  },
  {
    min: 0,
    label: 'Clean',
    classes: 'bg-emerald-400/10 text-emerald-300 ring-emerald-400/40',
  },
] as const;

export function riskBand(score: number) {
  return BANDS.find((band) => score >= band.min) ?? BANDS[BANDS.length - 1]!;
}
