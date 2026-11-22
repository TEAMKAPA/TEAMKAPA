import type { ReactNode } from 'react';

export type CardVariant = 'surface' | 'light' | 'navy' | 'navy-section';

interface CardProps {
  variant?: CardVariant;
  className?: string;
  children: ReactNode;
}

const CARD_VARIANTS: Record<CardVariant, string> = {
  surface: 'bg-[var(--bg-surface)] text-[var(--text-main)] border-[var(--border-subtle)]',
  light: 'bg-[var(--bg-light)] text-[var(--text-main)] border-[var(--border-subtle)]',
  navy: 'bg-[var(--bg-navy-dark)] text-white border-[var(--bg-navy-section)]',
  'navy-section': 'bg-[var(--bg-navy-section)] text-white border-[#14377A]',
};

export function Card({ variant = 'surface', className = '', children }: CardProps) {
  return (
    <div className={`rounded-lg border shadow-none ${CARD_VARIANTS[variant]} ${className}`}>
      {children}
    </div>
  );
}

interface CardHeaderProps {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export function CardHeader({ title, subtitle, action, className = '' }: CardHeaderProps) {
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border-subtle)] px-6 py-4 ${className}`}
    >
      <div className="min-w-0">
        <div className="text-base font-normal text-[var(--text-main)]">{title}</div>
        {subtitle && (
          <div className="mt-0.5 text-sm font-normal text-[#52555A]">{subtitle}</div>
        )}
      </div>
      {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
    </div>
  );
}
