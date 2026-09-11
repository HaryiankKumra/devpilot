import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <section className="rounded-lg border border-line bg-surface p-8">
      <h1 className="text-xl font-semibold text-fg">Page not found</h1>
      <p className="mt-2 text-sm text-muted">The page you asked for does not exist.</p>
      <Link
        to="/dashboard"
        className="mt-4 inline-block text-sm font-medium text-fg underline"
      >
        Back to the dashboard
      </Link>
    </section>
  );
}
