import { useEffect, useRef } from 'react';
import type { FieldAssessment, FieldSource } from '../../types/auditReport';
import { CONFIDENCE_STYLES } from '../audit/ConfidenceBadge';

interface PageOverlayProps {
  fields: FieldAssessment[];
  skippedSources?: Array<{ source: FieldSource; reason: string }>;
  currentPage: number;
  selectedFieldId: string | null;
  focusedSource?: { source: FieldSource; label?: string } | null;
  showAllBoxes: boolean;
  fieldLabels: ReadonlyMap<string, string>;
  onSelectField: (fieldId: string) => void;
}

export function PageOverlay({
  fields,
  skippedSources = [],
  currentPage,
  selectedFieldId,
  focusedSource,
  showAllBoxes,
  fieldLabels,
  onSelectField,
}: PageOverlayProps) {
  const activeBoxRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (activeBoxRef.current && typeof activeBoxRef.current.scrollIntoView === 'function') {
      activeBoxRef.current.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
        inline: 'nearest',
      });
    }
  }, [selectedFieldId, currentPage]);

  const boxesOnPage = fields.flatMap((assessment) =>
    assessment.sources
      .filter((src) => src.bbox && (src.page ?? 1) === currentPage)
      .map((src, idx) => ({
        key: `${assessment.field_id}-${idx}`,
        assessment,
        bbox: src.bbox!,
      })),
  );

  const visibleBoxes = showAllBoxes
    ? boxesOnPage
    : boxesOnPage.filter((item) => item.assessment.field_id === selectedFieldId);

  const skippedOnPage = skippedSources.filter(
    (item) => item.source.bbox && (item.source.page ?? 1) === currentPage,
  );

  return (
    <div className="pointer-events-none absolute inset-0" aria-label="Nakładka obszarów pól (bbox)">
      {showAllBoxes &&
        skippedOnPage.map((item, idx) => {
          const b = item.source.bbox!;
          const isFocused =
            focusedSource?.source.element_id &&
            focusedSource.source.element_id === item.source.element_id;
          return (
            <div
              key={`skipped-${idx}`}
              title={`Pominięto: ${item.reason}`}
              style={{
                left: `${b.x0 * 100}%`,
                top: `${b.y0 * 100}%`,
                width: `${Math.max((b.x1 - b.x0) * 100, 1)}%`,
                height: `${Math.max((b.y1 - b.y0) * 100, 1)}%`,
              }}
              className={`absolute rounded-sm border-2 border-dashed transition-all ${
                isFocused
                  ? 'z-30 border-slate-900 bg-slate-500/20 ring-4 ring-slate-700/25'
                  : 'z-10 border-slate-400/60 bg-slate-300/10'
              }`}
            >
              {isFocused && (
                <span className="absolute -top-7 left-0 max-w-[260px] truncate rounded-md bg-slate-900 px-2 py-0.5 text-xs font-medium text-white shadow-md">
                  Pominięto: {item.source.text ?? item.reason}
                </span>
              )}
            </div>
          );
        })}

      {visibleBoxes.map(({ key, assessment, bbox }) => {
        const isSelected = assessment.field_id === selectedFieldId;
        const style = CONFIDENCE_STYLES[assessment.level];
        const label = fieldLabels.get(assessment.field_id) ?? assessment.tech_name;
        const pct = Math.round(assessment.confidence * 100);

        // Niewybrane ramki są dyskretne, żeby nie zasłaniać tekstu dokumentu; wybrana ma mocny fokus.
        const boxTone =
          assessment.level === 'HIGH'
            ? isSelected
              ? 'bg-emerald-500/25 border-emerald-600'
              : 'bg-emerald-500/5 border-emerald-500/55 hover:bg-emerald-500/15'
            : assessment.level === 'MEDIUM'
              ? isSelected
                ? 'bg-amber-500/30 border-amber-600'
                : 'bg-amber-500/10 border-amber-500/75 hover:bg-amber-500/20'
              : isSelected
                ? 'bg-rose-500/30 border-rose-600'
                : 'bg-rose-500/12 border-rose-500/80 hover:bg-rose-500/22';

        return (
          <button
            key={key}
            ref={isSelected ? activeBoxRef : undefined}
            type="button"
            onClick={() => onSelectField(assessment.field_id)}
            data-testid={`bbox-${assessment.tech_name}`}
            data-selected={isSelected ? 'true' : 'false'}
            aria-label={`Obszar pola ${label} (${pct}%)`}
            style={{
              left: `${bbox.x0 * 100}%`,
              top: `${bbox.y0 * 100}%`,
              width: `${Math.max((bbox.x1 - bbox.x0) * 100, 1.5)}%`,
              height: `${Math.max((bbox.y1 - bbox.y0) * 100, 1.5)}%`,
            }}
            className={`pointer-events-auto absolute rounded-sm border-2 transition-all focus-visible:outline-none ${boxTone} ${
              isSelected ? `z-30 ring-4 ${style.ring} shadow-md` : 'z-20'
            }`}
          >
            {isSelected && (
              <span
                className="pointer-events-none absolute -top-7 left-0 z-40 inline-flex max-w-[260px] items-center gap-1.5 truncate rounded-md px-2 py-0.5 text-xs font-semibold text-white shadow-md"
                style={{ backgroundColor: style.hex }}
              >
                <span className="truncate">{label}</span>
                <span className="tabular-nums opacity-90">{pct}%</span>
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
