import { useEffect, useRef } from 'react';
import type { ConfidenceLevel, FieldAssessment, FieldSource } from '../../types/auditReport';

const CONFIDENCE_STYLES: Record<ConfidenceLevel, { hex: string }> = {
  HIGH: { hex: '#059669' },
  MEDIUM: { hex: '#D97706' },
  LOW: { hex: '#CA0049' },
};

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
              className={`absolute border-2 border-dashed shadow-none transition-colors ${
                isFocused
                  ? 'z-30 border-[#04122F] bg-[#04122F]/15'
                  : 'z-10 border-[#9FA1A5] bg-[#F3F3F4]/25'
              }`}
            >
              {isFocused && (
                <span className="absolute -top-6 left-0 max-w-[260px] truncate bg-[#04122F] px-2 py-0.5 text-xs font-normal text-white">
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

        const boxTone =
          assessment.level === 'HIGH'
            ? isSelected
              ? 'bg-[#059669]/15 border-[#059669] border-2'
              : 'bg-transparent border-[#9FA1A5]/60 border hover:border-[#08255E]'
            : assessment.level === 'MEDIUM'
              ? isSelected
                ? 'bg-[#D97706]/18 border-[#D97706] border-2'
                : 'bg-transparent border-[#D97706]/55 border hover:border-[#D97706]'
              : isSelected
                ? 'bg-[#CA0049]/15 border-[#CA0049] border-2'
                : 'bg-[#CA0049]/5 border-[#CA0049]/70 border hover:border-[#CA0049]';

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
            className={`pointer-events-auto absolute shadow-none transition-colors focus-visible:outline-none ${boxTone} ${
              isSelected ? 'z-30 outline-2 outline-offset-1 outline-[#04122F]' : 'z-20'
            }`}
          >
            {isSelected && (
              <span
                className="pointer-events-none absolute -top-6 left-0 z-40 inline-flex max-w-[260px] items-center gap-1.5 truncate px-2 py-0.5 text-xs font-normal text-white"
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
