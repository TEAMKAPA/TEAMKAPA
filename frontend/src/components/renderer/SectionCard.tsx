import type { FieldQuestion, LeafField, SectionField } from '../../types/coiForm';
import type { FieldAssessment, Issue } from '../../types/auditReport';
import {
  describeStateQuery,
  evaluateStates,
  makeAnswerKey,
  type AnswerValue,
  type FormAnswers,
} from '../../lib/conditionEvaluator';
import { FieldFactory } from './FieldFactory';

interface SectionCardProps {
  tabTitle: string;
  section: SectionField;
  visibleQuestions: FieldQuestion[];
  mode: 'review' | 'interactive';
  assessmentsById: ReadonlyMap<string, FieldAssessment>;
  liveIssuesByFieldId: ReadonlyMap<string, Issue[]>;
  selectedFieldId: string | null;
  reviewedIds: ReadonlySet<string>;
  modifiedIds: ReadonlySet<string>;
  onSelectField: (fieldId: string) => void;
  onOpenDetails: (fieldId: string) => void;
  onUpdateField: (updated: LeafField) => void;
  onToggleReviewed: (fieldId: string) => void;
  answers: FormAnswers;
  onAnswerChange: (key: string, value: AnswerValue) => void;
  validationErrors: Record<string, string>;
  repeatingCount?: number;
  onChangeRepeatingCount?: (delta: number) => void;
}

export function SectionCard({
  tabTitle,
  section,
  visibleQuestions,
  mode,
  assessmentsById,
  liveIssuesByFieldId,
  selectedFieldId,
  reviewedIds,
  modifiedIds,
  onSelectField,
  onOpenDetails,
  onUpdateField,
  onToggleReviewed,
  answers,
  onAnswerChange,
  validationErrors,
  repeatingCount = 1,
  onChangeRepeatingCount,
}: SectionCardProps) {
  const sectionEval = evaluateStates(section.states, answers, false);

  if (mode === 'interactive' && !sectionEval.visible) return null;
  if (visibleQuestions.length === 0) return null;

  const isRepeating = section.type === 'REPEATING_SECTION';
  const rowIndices =
    mode === 'interactive' && isRepeating
      ? Array.from({ length: Math.max(1, repeatingCount) }, (_, idx) => idx)
      : [undefined];

  return (
    <section className="border-t border-[#E5E7EB] pt-4 first:border-t-0 first:pt-0">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-[17px] font-normal leading-6 text-[#0D0E0E]">{section.title}</h3>
          {isRepeating && (
            <span className="text-[13px] font-normal text-[#374151]">(sekcja powtarzalna)</span>
          )}
          {section.states && section.states.length > 0 && (
            <span className="text-[13px] font-normal text-[#374151]">
              Warunek: {describeStateQuery(section.states[0].query)}
            </span>
          )}
        </div>

        {mode === 'interactive' && isRepeating && onChangeRepeatingCount && (
          <button
            type="button"
            onClick={() => onChangeRepeatingCount(1)}
            className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-3 py-1.5 text-sm font-normal text-[#0D0E0E] hover:bg-[#F3F3F4]"
          >
            Dodaj wpis
          </button>
        )}
      </div>

      <div className="space-y-3">
        {rowIndices.map((repIdx) => (
          <div
            key={repIdx ?? 'single'}
            className={
              repIdx !== undefined
                ? 'rounded-lg border border-[#E5E7EB] p-4'
                : 'divide-y divide-[#E5E7EB]'
            }
          >
            {repIdx !== undefined && (
              <div className="mb-2 flex items-center justify-between border-b border-[#E5E7EB] pb-2 text-sm font-normal text-[#52555A]">
                <span>Pozycja #{repIdx + 1}</span>
                {repeatingCount > 1 && onChangeRepeatingCount && (
                  <button
                    type="button"
                    onClick={() => onChangeRepeatingCount(-1)}
                    className="text-xs font-normal text-[#CA0049] hover:underline"
                  >
                    Usuń pozycję
                  </button>
                )}
              </div>
            )}

            {visibleQuestions.map((fq) => {
              const leaf = fq.field;
              const fieldStates = 'states' in leaf ? leaf.states : undefined;
              const baseReadOnly = 'readOnly' in leaf ? Boolean(leaf.readOnly) : false;
              const evalState = evaluateStates(fieldStates, answers, baseReadOnly, repIdx);

              if (mode === 'interactive' && !evalState.visible) return null;

              const answerKey = makeAnswerKey(tabTitle, section.techName, leaf.techName, repIdx);

              return (
                <FieldFactory
                  key={leaf.id}
                  field={leaf}
                  mode={mode}
                  assessment={assessmentsById.get(leaf.id)}
                  liveIssues={liveIssuesByFieldId.get(leaf.id)}
                  isSelected={selectedFieldId === leaf.id}
                  isReviewed={reviewedIds.has(leaf.id)}
                  isModified={modifiedIds.has(leaf.id)}
                  onSelect={onSelectField}
                  onOpenDetails={onOpenDetails}
                  onUpdateField={onUpdateField}
                  onToggleReviewed={onToggleReviewed}
                  value={answers[answerKey]}
                  onChangeValue={(val) => onAnswerChange(answerKey, val)}
                  dynamicReadOnly={evalState.readOnly}
                  validationError={validationErrors[answerKey]}
                />
              );
            })}
          </div>
        ))}
      </div>
    </section>
  );
}
