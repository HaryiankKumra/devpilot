interface PagePlaceholderProps {
  title: string;
  /** What this page will do, stated plainly. */
  description: string;
  /** The milestone that implements it, so the roadmap is visible in the app. */
  milestone: string;
}

/**
 * Honest stand-in for a page that is routed but not yet built.
 *
 * Deliberately says "not implemented" rather than showing placeholder data:
 * fake content in a portfolio project is indistinguishable from a bug.
 */
export function PagePlaceholder({ title, description, milestone }: PagePlaceholderProps) {
  return (
    <section className="rounded-lg border border-dashed border-slate-300 bg-white p-8">
      <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
      <p className="mt-2 max-w-prose text-sm text-slate-600">{description}</p>
      <p className="mt-4 text-xs font-medium uppercase tracking-wide text-slate-400">
        Not implemented yet · {milestone}
      </p>
    </section>
  );
}
