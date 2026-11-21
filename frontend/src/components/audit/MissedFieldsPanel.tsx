import { AlertCircle, AlertTriangle, ArrowRight, EyeOff, Info, MapPin } from 'lucide-react';
import {
  CHANNEL_LABELS,
  type FieldSource,
  type Issue,
  type Severity,
  type SkippedElement,
} from '../../types/auditReport';
import { Badge, type BadgeVariant } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card, CardHeader } from '../ui/Card';

interface MissedFieldsPanelProps {
  skippedElements: SkippedElement[];
  issues: Issue[];
  onSelectField?: (fieldId: string) => void;
  onFocusSource?: (source: FieldSource, label?: string) => void;
}

const SEVERITY_CONFIG: Record<
  Severity,
  { label: string; variant: BadgeVariant; icon: typeof AlertCircle; rowBg: string }
> = {
  ERROR: {
    label: 'Błąd krytyczny',
    variant: 'danger',
    icon: AlertCircle,
    rowBg: 'bg-rose-50/70 border-rose-200',
  },
  WARNING: {
    label: 'Ostrzeżenie',
    variant: 'warning',
    icon: AlertTriangle,
    rowBg: 'bg-amber-50/60 border-amber-200',
  },
  INFO: {
    label: 'Informacja',
    variant: 'info',
    icon: Info,
    rowBg: 'bg-blue-50/50 border-blue-200',
  },
};

export function MissedFieldsPanel({
  skippedElements,
  issues,
  onSelectField,
  onFocusSource,
}: MissedFieldsPanelProps) {
  const sortedIssues = [...issues].sort((a, b) => {
    const rank: Record<Severity, number> = { ERROR: 0, WARNING: 1, INFO: 2 };
    return rank[a.severity] - rank[b.severity];
  });

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title={`Uwagi i wykryte rozbieżności (${sortedIssues.length})`}
          subtitle="Błędy krytyczne (ERROR) blokują zatwierdzenie formularza do importu COI"
        />
        <div className="p-5">
          {sortedIssues.length === 0 ? (
            <p className="py-4 text-center text-sm font-medium text-emerald-700">
              Brak wykrytych błędów i ostrzeżeń reguł COI.
            </p>
          ) : (
            <ul className="space-y-3">
              {sortedIssues.map((issue, idx) => {
                const cfg = SEVERITY_CONFIG[issue.severity];
                const Icon = cfg.icon;
                return (
                  <li
                    key={`${issue.code}-${issue.field_id ?? 'form'}-${idx}`}
                    className={`rounded-xl border p-4 text-sm ${cfg.rowBg}`}
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="flex items-start gap-3">
                        <Icon className="mt-0.5 h-5 w-5 shrink-0" />
                        <div className="space-y-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant={cfg.variant}>{cfg.label}</Badge>
                            <code className="rounded bg-white/80 px-2 py-0.5 text-xs font-medium text-slate-700">
                              {issue.code}
                            </code>
                          </div>
                          <p className="leading-relaxed text-slate-900">{issue.message}</p>
                        </div>
                      </div>
                      {issue.field_id && onSelectField && (
                        <Button
                          variant="secondary"
                          size="xs"
                          onClick={() => onSelectField(issue.field_id!)}
                        >
                          Przejdź do pola
                          <ArrowRight className="h-3.5 w-3.5" />
                        </Button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-2">
              <EyeOff className="h-5 w-5 text-slate-500" />
              Elementy pominięte ({skippedElements.length})
            </span>
          }
          subtitle="Fragmenty blankietu nieujęte w e-formularzu zgodnie ze standardem COI (np. adnotacje urzędu, podpis odręczny)"
        />
        <div className="p-5">
          {skippedElements.length === 0 ? (
            <p className="py-4 text-center text-sm text-slate-500">
              Brak pominiętych elementów w tym dokumencie.
            </p>
          ) : (
            <ul className="space-y-3">
              {skippedElements.map((item, idx) => (
                <li
                  key={`${item.source.element_id ?? idx}`}
                  className="rounded-xl border border-slate-200 bg-slate-50/70 p-4 text-sm"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-1.5">
                      <div className="font-medium text-slate-900">{item.reason}</div>
                      {item.source.text && (
                        <div className="text-xs italic text-slate-600">„{item.source.text}”</div>
                      )}
                      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                        <Badge variant="neutral">
                          {CHANNEL_LABELS[item.source.channel] ?? item.source.channel}
                        </Badge>
                        {item.source.page && <span>Strona {item.source.page}</span>}
                      </div>
                    </div>
                    {item.source.bbox && onFocusSource && (
                      <Button
                        variant="secondary"
                        size="xs"
                        onClick={() => onFocusSource(item.source, item.reason)}
                      >
                        <MapPin className="h-3.5 w-3.5" />
                        Pokaż na stronie
                      </Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>
    </div>
  );
}
