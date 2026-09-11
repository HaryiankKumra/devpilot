import { AuthLayout } from '@/components/AuthLayout';
import { FormField } from '@/components/FormField';
import { useAuth, useLogin, useRegister } from '@/features/auth/useAuth';
import { type FormEvent, useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';

// Must match the backend's PASSWORD_MIN_LENGTH. Checked here purely to give
// immediate feedback -- the API enforces it regardless, and that check is the
// one that counts.
const PASSWORD_MIN_LENGTH = 12;

export function RegisterPage() {
  const navigate = useNavigate();
  const { hasToken } = useAuth();
  const register = useRegister();
  const login = useLogin();

  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [passwordTouched, setPasswordTouched] = useState(false);

  if (hasToken) {
    return <Navigate to="/dashboard" replace />;
  }

  const passwordTooShort = password.length > 0 && password.length < PASSWORD_MIN_LENGTH;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password.length < PASSWORD_MIN_LENGTH) {
      setPasswordTouched(true);
      return;
    }

    register.mutate(
      { email, password, ...(fullName ? { full_name: fullName } : {}) },
      {
        // Registration does not return a token by design, so sign in with the
        // credentials just used rather than making the user retype them.
        onSuccess: () =>
          login.mutate(
            { email, password },
            { onSuccess: () => navigate('/dashboard', { replace: true }) },
          ),
      },
    );
  }

  const isSubmitting = register.isPending || login.isPending;
  const failure = register.error ?? login.error;

  return (
    <AuthLayout
      title="Create an account"
      subtitle="Connect a repository and start reviewing pull requests."
      footer={
        <>
          Already registered?{' '}
          <Link to="/login" className="font-medium text-fg underline">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <FormField
          label="Full name"
          type="text"
          name="name"
          autoComplete="name"
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
          hint="Optional."
        />
        <FormField
          label="Email"
          type="email"
          name="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <FormField
          label="Password"
          type="password"
          name="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          onBlur={() => setPasswordTouched(true)}
          hint={`At least ${PASSWORD_MIN_LENGTH} characters. A passphrase works well.`}
          error={
            passwordTouched && passwordTooShort
              ? `Use at least ${PASSWORD_MIN_LENGTH} characters.`
              : undefined
          }
        />

        {failure && (
          <p
            role="alert"
            className="rounded-md bg-severity-critical/10 px-3 py-2 text-sm text-muted"
          >
            {failure.message}
          </p>
        )}

        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-ink shadow-glow transition hover:bg-accent-hover disabled:opacity-60"
        >
          {isSubmitting ? 'Creating account…' : 'Create account'}
        </button>
      </form>
    </AuthLayout>
  );
}
