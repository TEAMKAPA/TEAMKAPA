import type { ReactNode } from 'react';

export type BadgeVariant = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'purple';

interface BadgeProps {
  variant?: BadgeVariant;
  className?: string;
  children: ReactNode;
  title?: string;
}

const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  neutral: 'bg-slate-100 text-slate-700 border-slate-200/80',
  info: 'bg-blue-50 text-blue-800 border-blue-200/80',
  success: 'bg-emerald-50 text-emerald-800 border-emerald-200/80',
  warning: 'bg-amber-50 text-amber-900 border-amber-300/80',
  danger: 'bg-rose-50 text-rose-800 border-rose-200/80',
  purple: 'bg-indigo-50 text-indigo-800 border-indigo-200/80',
};

export function Badge({ variant = 'neutral', className = '', children, title }: BadgeProps) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium leading-5 whitespace-nowrap ${VARIANT_CLASSES[variant]} ${className}`}
    >
      {children}
    </span>
  );
}
