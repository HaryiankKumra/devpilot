/**
 * Authentication state and actions.
 *
 * There is no React context here on purpose. The token already lives in one
 * module (`lib/authToken`) that components subscribe to, and the user object is
 * server state owned by React Query. Wrapping both in a context would add a
 * provider and a second copy of state that can disagree with the cache.
 */

import {
  fetchCurrentUser,
  login as loginRequest,
  register as registerRequest,
  type AuthenticatedUser,
  type LoginCredentials,
  type RegistrationDetails,
} from '@/features/auth/api';
import { ApiError } from '@/lib/apiClient';
import { getAccessToken, setAccessToken, subscribeToToken } from '@/lib/authToken';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useSyncExternalStore } from 'react';

export const currentUserQueryKey = ['auth', 'me'] as const;

/** Re-renders whenever the stored token changes, including after logout. */
export function useAccessToken(): string | null {
  return useSyncExternalStore(subscribeToToken, getAccessToken, () => null);
}

export interface AuthState {
  user: AuthenticatedUser | undefined;
  /** True while the stored token is being exchanged for a user. */
  isLoading: boolean;
  isAuthenticated: boolean;
  hasToken: boolean;
}

export function useAuth(): AuthState {
  const token = useAccessToken();

  const { data, isLoading, isError } = useQuery({
    queryKey: currentUserQueryKey,
    queryFn: fetchCurrentUser,
    // Without a token there is nothing to ask about, and firing the request
    // anyway would produce a guaranteed 401 on every page load.
    enabled: token !== null,
    // A 401 here means the token is expired or revoked; retrying cannot help.
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  // A token the API rejects is worse than no token: it makes the UI look signed
  // in while every request fails. Drop it so the user is sent to login.
  // Done in an effect because clearing it updates an external store, and doing
  // that during render would re-enter rendering from inside itself.
  useEffect(() => {
    if (token !== null && isError) {
      setAccessToken(null);
    }
  }, [token, isError]);

  return {
    user: data,
    isLoading: token !== null && isLoading,
    isAuthenticated: token !== null && data !== undefined,
    hasToken: token !== null,
  };
}

export function useLogin() {
  const queryClient = useQueryClient();

  return useMutation<void, ApiError, LoginCredentials>({
    mutationFn: async (credentials) => {
      const { access_token } = await loginRequest(credentials);
      setAccessToken(access_token);
      // Discard anything cached for the previous session before the new user's
      // data is fetched, so one account never briefly sees another's.
      await queryClient.invalidateQueries();
    },
  });
}

export function useRegister() {
  return useMutation<AuthenticatedUser, ApiError, RegistrationDetails>({
    mutationFn: registerRequest,
  });
}

export function useLogout() {
  const queryClient = useQueryClient();

  return () => {
    setAccessToken(null);
    // Remove rather than invalidate: invalidating would refetch immediately,
    // and every one of those requests would 401.
    queryClient.clear();
  };
}
