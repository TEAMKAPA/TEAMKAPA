import type { ReactNode } from 'react';

export type BadgeVariant = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'purple';

interface BadgeProps {
  variant?: BadgeVariant;
  className?: string;
  children: ReactNode;
  title?: string;
}

const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  neutral: 'bg-[var(--bg-light)] text-[var(--text-main)] border-[var(--border-subtle)]',
  info: 'bg-[#EEF2FA] text-[var(--bg-navy-section)] border-[#C9D6EE]',
  success: 'bg-[#ECFDF5] text-[#065F46] border-[#A7F3D0]',
  warning: 'bg-[#FFFBEB] text-[#92400E] border-[#FDE68A]',
  danger: 'bg-[#FFF1F2] text-[#9F1239] border-[#FECDD3]',
  purple: 'bg-[#EEF2FA] text-[var(--bg-navy-dark)] border-[#C9D6EE]',
};

export function Badge({ variant = 'neutral', className = '', children, title }: BadgeProps) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-0.5 text-xs font-normal leading-5 shadow-none whitespace-nowrap ${VARIANT_CLASSES[variant]} ${className}`}
    >
      {children}
    </span>
  );
}
