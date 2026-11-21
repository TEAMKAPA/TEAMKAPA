import { useState } from 'react';
import { AlertCircle, AlertTriangle, CheckCircle2, ChevronDown, ChevronUp } from 'lucide-react';
import {
  SIGNAL_LABELS,
  type ConfidenceLevel,
  type FieldAssessment,
} from '../../types/auditReport';

interface ConfidenceBadgeProps {
  level: ConfidenceLevel;
  confidence: number;
  signals?: Record<string, number>;
  expandable?: boolean;
  compact?: boolean;
}

export const CONFIDENCE_STYLES: Record<
  ConfidenceLevel,
  {
    label: string;
    shortLabel: string;
    pill: string;
    dot: string;
    border: string;
    ring: string;
    hex: string;
  }
> = {
  HIGH: {
    label: 'Wysoka pewność',
    shortLabel: 'Pewne',
    pill: 'bg-emerald-50 text-emerald-800 border-emerald-200',
    dot: 'bg-emerald-500',
    border: 'border-l-emerald-500',
    ring: 'ring-emerald-500/40',
    hex: '#059669',
  },
  MEDIUM: {
    label: 'Wymaga przejrzenia',
    shortLabel: 'Sprawdź',
    pill: 'bg-amber-50 text-amber-900 border-amber-300',
    dot: 'bg-amber-500',
    border: 'border-l-amber-500',
    ring: 'ring-amber-500/40',
    hex: '#d97706',
  },
  LOW: {
    label: 'Niska pewność — wymaga decyzji',
    shortLabel: 'Niska',
    pill: 'bg-rose-50 text-rose-800 border-rose-300',
    dot: 'bg-rose-500',
    border: 'border-l-rose-500',
    ring: 'ring-rose-500/40',
    hex: '#e11d48',
  },
};

export function ConfidenceBadge({
  level,
  confidence,
  signals,
  expandable = false,
  compact = false,
}: ConfidenceBadgeProps) {
  const [open, setOpen] = useState(false);
  const style = CONFIDENCE_STYLES[level];
  const pct = Math.round(confidence * 100);
  const signalEntries = signals ? Object.entries(signals) : [];

  const Icon =
    level === 'HIGH' ? CheckCircle2 : level === 'MEDIUM' ? AlertTriangle : AlertCircle;

  const content = (
    <>
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="tabular-nums">{pct}%</span>
      {!compact && <span className="hidden sm:inline">· {style.shortLabel}</span>}
      {expandable && signalEntries.length > 0 &&
        (open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />)}
    </>
  );

  return (
    <div className="relative inline-flex flex-col items-end">
      {expandable && signalEntries.length > 0 ? (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setOpen((v) => !v);
          }}
          title={`${style.label} (${pct}%) — kliknij, aby zobaczyć składowe oceny`}
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold transition-opacity hover:opacity-85 ${style.pill}`}
        >
          {content}
        </button>
      ) : (
        <span
          title={`${style.label} (${pct}%)`}
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${style.pill}`}
        >
          {content}
        </span>
      )}

      {open && signalEntries.length > 0 && (
        <div
          onClick={(e) => e.stopPropagation()}
          className="absolute top-full right-0 z-30 mt-2 w-72 rounded-xl border border-slate-200 bg-white p-3.5 text-left shadow-xl"
        >
          <div className="mb-2.5 text-xs font-semibold text-slate-800">
            Składowe pewności pola
          </div>
          <div className="space-y-2.5">
            {signalEntries.map(([name, value]) => {
              const scorePct = Math.round(value * 100);
              const barColor =
                value >= 0.85
                  ? 'bg-emerald-500'
                  : value >= 0.5
                    ? 'bg-amber-500'
                    : 'bg-rose-500';
              return (
                <div key={name} className="space-y-1">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-600">{SIGNAL_LABELS[name] ?? name}</span>
                    <span className="font-semibold text-slate-900 tabular-nums">{scorePct}%</span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                    <div
                      className={`h-full rounded-full ${barColor}`}
                      style={{ width: `${scorePct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export function getAssessmentByFieldId(
  fields: FieldAssessment[],
): ReadonlyMap<string, FieldAssessment> {
  return new Map(fields.map((f) => [f.field_id, f]));
}
