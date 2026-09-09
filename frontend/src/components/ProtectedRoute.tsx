import { useAuth } from '@/features/auth/useAuth';
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';

/**
 * Gate for routes that require a signed-in user.
 *
 * This is a usability boundary, not a security one: the browser can be made to
 * render anything. Authorisation is enforced by the API on every request, and
 * this only spares the user a screen full of failed requests.
 */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { hasToken, isLoading, isAuthenticated } = useAuth();
  const location = useLocation();

  if (!hasToken) {
    // Remember where they were going so login can send them back there.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  if (isLoading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <p className="text-sm text-slate-500" role="status">
          Loading your account…
        </p>
      </div>
    );
  }

  if (!isAuthenticated) {
    // The token was rejected; `useAuth` has already cleared it.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}
