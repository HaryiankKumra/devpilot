/** API bindings for the authentication endpoints. */

import { apiFetch } from '@/lib/apiClient';

export interface AuthenticatedUser {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface RegistrationDetails extends LoginCredentials {
  full_name?: string;
}

export function login(credentials: LoginCredentials): Promise<TokenResponse> {
  return apiFetch<TokenResponse>('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify(credentials),
    // There is no token yet, and sending a stale one would be misleading.
    authenticated: false,
  });
}

export function register(details: RegistrationDetails): Promise<AuthenticatedUser> {
  return apiFetch<AuthenticatedUser>('/api/v1/auth/register', {
    method: 'POST',
    body: JSON.stringify(details),
    authenticated: false,
  });
}

export function fetchCurrentUser(): Promise<AuthenticatedUser> {
  return apiFetch<AuthenticatedUser>('/api/v1/auth/me');
}
