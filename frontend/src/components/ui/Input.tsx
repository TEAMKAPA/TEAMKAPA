import type {
  InputHTMLAttributes,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react';

export const FLAT_INPUT_BASE =
  'w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3.5 py-2.5 text-sm font-normal text-[var(--text-main)] placeholder:text-[var(--separator)] shadow-none transition-colors focus:border-[var(--bg-navy-section)] focus:outline-none disabled:cursor-not-allowed disabled:bg-[var(--bg-light)] disabled:text-[var(--separator)]';

export const FLAT_INPUT_ERROR = 'border-[var(--accent-crimson)] focus:border-[var(--accent-crimson)]';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  hasError?: boolean;
}

export function Input({ hasError = false, className = '', ...rest }: InputProps) {
  return (
    <input
      className={`${FLAT_INPUT_BASE} ${hasError ? FLAT_INPUT_ERROR : ''} ${className}`}
      {...rest}
    />
  );
}

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  hasError?: boolean;
}

export function Textarea({ hasError = false, className = '', ...rest }: TextareaProps) {
  return (
    <textarea
      className={`${FLAT_INPUT_BASE} ${hasError ? FLAT_INPUT_ERROR : ''} ${className}`}
      {...rest}
    />
  );
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  hasError?: boolean;
}

export function Select({ hasError = false, className = '', ...rest }: SelectProps) {
  return (
    <select
      className={`${FLAT_INPUT_BASE} ${hasError ? FLAT_INPUT_ERROR : ''} ${className}`}
      {...rest}
    />
  );
}
