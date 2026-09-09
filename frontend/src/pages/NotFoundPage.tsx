import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-8">
      <h1 className="text-xl font-semibold text-slate-900">Page not found</h1>
      <p className="mt-2 text-sm text-slate-600">
        The page you asked for does not exist.
      </p>
      <Link
        to="/dashboard"
        className="mt-4 inline-block text-sm font-medium text-slate-900 underline"
      >
        Back to the dashboard
      </Link>
    </section>
  );
}
