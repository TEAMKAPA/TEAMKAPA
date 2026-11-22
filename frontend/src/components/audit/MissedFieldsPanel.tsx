import {
  CHANNEL_LABELS,
  type FieldSource,
  type Issue,
  type Severity,
  type SkippedElement,
} from '../../types/auditReport';

interface MissedFieldsPanelProps {
  skippedElements: SkippedElement[];
  issues: Issue[];
  onSelectField?: (fieldId: string) => void;
  onFocusSource?: (source: FieldSource, label?: string) => void;
}

const SEVERITY_LABELS: Record<Severity, string> = {
  ERROR: 'Błąd krytyczny',
  WARNING: 'Ostrzeżenie',
  INFO: 'Informacja',
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
    <div className="space-y-6">
      <div>
        <h3 className="border-b border-[#E5E7EB] pb-2 text-[18px] font-normal text-[#0D0E0E]">
          Uwagi i rozbieżności ({sortedIssues.length})
        </h3>

        {sortedIssues.length === 0 ? (
          <p className="py-4 text-sm font-normal text-[#065F46]">
            Brak wykrytych błędów i ostrzeżeń.
          </p>
        ) : (
          <ul className="divide-y divide-[#E5E7EB]">
            {sortedIssues.map((issue, idx) => (
              <li
                key={`${issue.code}-${issue.field_id ?? 'form'}-${idx}`}
                className="flex flex-wrap items-start justify-between gap-4 py-3.5"
              >
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="text-xs font-normal text-[#52555A]">
                    {SEVERITY_LABELS[issue.severity]} · {issue.code}
                  </div>
                  <p className="text-[15px] font-normal text-[#0D0E0E]">{issue.message}</p>
                </div>
                {issue.field_id && onSelectField && (
                  <button
                    type="button"
                    onClick={() => onSelectField(issue.field_id!)}
                    className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-3 py-1.5 text-sm font-normal text-[#08255E] hover:bg-[#F3F3F4]"
                  >
                    Przejdź do pola
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h3 className="border-b border-[#E5E7EB] pb-2 text-[18px] font-normal text-[#0D0E0E]">
          Elementy pominięte ({skippedElements.length})
        </h3>

        {skippedElements.length === 0 ? (
          <p className="py-4 text-sm font-normal text-[#52555A]">Brak pominiętych elementów.</p>
        ) : (
          <ul className="divide-y divide-[#E5E7EB]">
            {skippedElements.map((item, idx) => (
              <li
                key={`${item.source.element_id ?? idx}`}
                className="flex flex-wrap items-start justify-between gap-4 py-3.5"
              >
                <div className="space-y-1">
                  <div className="text-[15px] font-normal text-[#0D0E0E]">{item.reason}</div>
                  {item.source.text && (
                    <div className="text-sm font-normal text-[#52555A]">„{item.source.text}”</div>
                  )}
                  <div className="text-xs font-normal text-[#52555A]">
                    {CHANNEL_LABELS[item.source.channel] ?? item.source.channel}
                    {item.source.page ? ` · Strona ${item.source.page}` : ''}
                  </div>
                </div>
                {item.source.bbox && onFocusSource && (
                  <button
                    type="button"
                    onClick={() => onFocusSource(item.source, item.reason)}
                    className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-3 py-1.5 text-sm font-normal text-[#08255E] hover:bg-[#F3F3F4]"
                  >
                    Pokaż na stronie
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
