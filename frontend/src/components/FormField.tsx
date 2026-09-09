import type { InputHTMLAttributes } from 'react';
import { useId } from 'react';

interface FormFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string;
  /** Validation message shown beneath the field, if any. */
  error?: string | undefined;
  hint?: string | undefined;
}

/**
 * A labelled text input.
 *
 * The generated id ties the label to the input, and `aria-describedby` ties any
 * error to it as well, so a screen reader announces the problem when focus
 * lands on the field rather than leaving it as unrelated red text.
 */
export function FormField({ label, error, hint, ...inputProps }: FormFieldProps) {
  const id = useId();
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;
  const describedBy = [error ? errorId : null, hint ? hintId : null]
    .filter(Boolean)
    .join(' ');

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={[
          'w-full rounded-md border px-3 py-2 text-sm shadow-sm outline-none transition',
          'focus:ring-2 focus:ring-slate-900/10',
          error
            ? 'border-red-400 focus:border-red-500'
            : 'border-slate-300 focus:border-slate-500',
        ].join(' ')}
        {...inputProps}
      />
      {hint && !error && (
        <p id={hintId} className="text-xs text-slate-500">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-xs text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
