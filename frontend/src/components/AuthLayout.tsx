import { Backdrop } from '@/components/Backdrop';
import { Wordmark } from '@/components/Wordmark';
import type { ReactNode } from 'react';

interface AuthLayoutProps {
  title: string;
  subtitle: string;
  children: ReactNode;
  footer: ReactNode;
}

/** Centred card shared by the sign-in and registration pages. */
export function AuthLayout({ title, subtitle, children, footer }: AuthLayoutProps) {
  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-12">
      <Backdrop />
      <div className="w-full max-w-sm animate-rise">
        <div className="mb-8 flex flex-col items-center text-center">
          <Wordmark size="lg" />
          <p className="mt-2 font-mono text-xs text-dim">
            pull request review, with the receipts
          </p>
        </div>

        <div className="rounded-xl border border-line bg-surface/80 p-6 shadow-card backdrop-blur">
          <h2 className="text-lg font-semibold text-fg">{title}</h2>
          <p className="mt-1 text-sm text-muted">{subtitle}</p>
          <div className="mt-6">{children}</div>
        </div>

        <p className="mt-6 text-center text-sm text-muted">{footer}</p>
      </div>
    </div>
  );
}
