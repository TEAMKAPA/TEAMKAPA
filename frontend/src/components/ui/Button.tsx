import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success' | 'navy';
export type ButtonSize = 'xs' | 'sm' | 'md';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children: ReactNode;
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  /** Główny przycisk CTA: wyłącznie #CA0049 z tekstem #FCE6EE */
  primary:
    'bg-[var(--accent-crimson)] text-[var(--text-on-crimson)] hover:bg-[#A8003D] active:bg-[#8F0034] border-transparent focus-visible:outline-[var(--accent-crimson)]',
  secondary:
    'bg-[var(--bg-surface)] text-[var(--text-main)] hover:bg-[var(--bg-light)] border-[var(--border-subtle)] focus-visible:outline-[var(--bg-navy-section)]',
  ghost:
    'bg-transparent text-[var(--text-main)] hover:bg-[var(--bg-light)] border-transparent focus-visible:outline-[var(--bg-navy-section)]',
  navy:
    'bg-[var(--bg-navy-dark)] text-white hover:bg-[var(--bg-navy-section)] border-transparent focus-visible:outline-[var(--bg-navy-dark)]',
  danger:
    'bg-[var(--bg-surface)] text-[#9F1239] hover:bg-[#FFF1F2] border-[#FECDD3] focus-visible:outline-[#9F1239]',
  success:
    'bg-[var(--bg-navy-section)] text-white hover:bg-[var(--bg-navy-dark)] border-transparent focus-visible:outline-[var(--bg-navy-section)]',
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
  xs: 'py-1.5 px-3 text-sm gap-1.5 rounded-lg',
  sm: 'py-2 px-4 text-sm gap-2 rounded-lg',
  md: 'py-3 px-6 text-[15px] gap-2 rounded-lg',
};

export function Button({
  variant = 'secondary',
  size = 'md',
  className = '',
  type = 'button',
  disabled,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled}
      className={`inline-flex items-center justify-center border font-normal leading-tight shadow-none transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap ${VARIANT_CLASSES[variant]} ${SIZE_CLASSES[size]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
