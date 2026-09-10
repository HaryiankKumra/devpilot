/**
 * Risk score bands.
 *
 * These match `SEVERITY_FLOORS` in the backend's `risk.py` exactly. If the two
 * ever disagree, a review containing a critical finding could be scored 75 by
 * the backend and drawn as "medium" here — the number and the colour would
 * contradict each other, which is worse than showing neither.
 */
const BANDS = [
  { min: 75, label: 'Critical', classes: 'bg-red-100 text-red-900 ring-red-300' },
  { min: 50, label: 'High', classes: 'bg-orange-100 text-orange-900 ring-orange-300' },
  { min: 25, label: 'Medium', classes: 'bg-amber-100 text-amber-900 ring-amber-300' },
  { min: 1, label: 'Low', classes: 'bg-sky-100 text-sky-900 ring-sky-300' },
  { min: 0, label: 'Clean', classes: 'bg-emerald-100 text-emerald-900 ring-emerald-300' },
] as const;

export function riskBand(score: number) {
  return BANDS.find((band) => score >= band.min) ?? BANDS[BANDS.length - 1]!;
}
