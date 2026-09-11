import { Link } from 'react-router-dom';

/**
 * The name, with a small mark beside it: a caret in a bracket, which is what
 * a diff hunk header looks like and also what a cursor looks like. Drawn
 * inline so it needs no asset and inherits the text colour.
 */
export function Wordmark({ to = '/', size = 'sm' }: { to?: string; size?: 'sm' | 'lg' }) {
  const text = size === 'lg' ? 'text-2xl' : 'text-base';
  const mark = size === 'lg' ? 'h-7 w-7' : 'h-5 w-5';

  return (
    <Link
      to={to}
      className="inline-flex items-center gap-2 text-fg"
      aria-label="DevPilot home"
    >
      <svg viewBox="0 0 24 24" className={`${mark} text-accent`} aria-hidden="true">
        <path
          d="M4 4h5v2H6v12h3v2H4zM20 4h-5v2h3v12h-3v2h5z"
          fill="currentColor"
          opacity="0.55"
        />
        <path
          d="M9.5 15.5 12 8l2.5 7.5"
          stroke="currentColor"
          strokeWidth="2"
          fill="none"
        />
      </svg>
      <span className={`${text} font-semibold tracking-tight`}>
        Dev<span className="text-accent">Pilot</span>
      </span>
    </Link>
  );
}
