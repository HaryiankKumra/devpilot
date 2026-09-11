import { ApiStatusBadge } from '@/components/ApiStatusBadge';
import { Backdrop } from '@/components/Backdrop';
import { Wordmark } from '@/components/Wordmark';
import { useAuth, useLogout } from '@/features/auth/useAuth';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';

const NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/repositories', label: 'Repositories' },
  { to: '/settings', label: 'Settings' },
] as const;

function navLinkClass({ isActive }: { isActive: boolean }): string {
  return [
    'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
    isActive ? 'bg-raised text-fg' : 'text-muted hover:bg-raised/60 hover:text-fg',
  ].join(' ');
}

/** Chrome shared by every authenticated page: header, navigation, content slot. */
export function AppLayout() {
  const { user } = useAuth();
  const logout = useLogout();
  const navigate = useNavigate();

  function handleSignOut() {
    logout();
    navigate('/login', { replace: true });
  }

  return (
    <div className="min-h-screen">
      {/* Quieter than on the landing page: it sits behind data people read. */}
      <Backdrop intensity={0.45} />

      <header className="sticky top-0 z-20 border-b border-line bg-ink/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-4 px-4 py-3">
          <Wordmark />
          <nav aria-label="Primary" className="flex flex-1 flex-wrap gap-1">
            {NAV_ITEMS.map((item) => (
              <NavLink key={item.to} to={item.to} className={navLinkClass}>
                {item.label}
              </NavLink>
            ))}
          </nav>
          <ApiStatusBadge />
          {user && (
            <div className="flex items-center gap-3">
              <span className="hidden font-mono text-xs text-dim sm:inline">
                {user.email}
              </span>
              <button
                type="button"
                onClick={handleSignOut}
                className="rounded-md border border-line px-3 py-1.5 text-sm font-medium text-muted transition hover:border-muted hover:text-fg"
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-10">
        <Outlet />
      </main>
    </div>
  );
}
