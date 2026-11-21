import { Eye, Plus, Repeat, Trash2 } from 'lucide-react';
import type { FieldQuestion, SectionField } from '../../types/coiForm';
import type { FieldAssessment, Issue } from '../../types/auditReport';
import {
  describeStateQuery,
  evaluateStates,
  makeAnswerKey,
  type AnswerValue,
  type FormAnswers,
} from '../../lib/conditionEvaluator';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
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
  onToggleReviewed,
  answers,
  onAnswerChange,
  validationErrors,
  repeatingCount = 1,
  onChangeRepeatingCount,
}: SectionCardProps) {
  const sectionEval = evaluateStates(section.states, answers, false);

  if (mode === 'interactive' && !sectionEval.visible) {
    return null;
  }

  if (visibleQuestions.length === 0) {
    return null;
  }

  const isRepeating = section.type === 'REPEATING_SECTION';
  const rowIndices =
    mode === 'interactive' && isRepeating
      ? Array.from({ length: Math.max(1, repeatingCount) }, (_, idx) => idx)
      : [undefined];

  return (
    <section className="rounded-2xl border border-slate-200/90 bg-slate-50/70 p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2.5">
          <h3 className="text-base font-semibold text-slate-900">{section.title}</h3>
          {isRepeating && (
            <Badge variant="info">
              <Repeat className="h-3 w-3" />
              Sekcja powtarzalna
            </Badge>
          )}
          {section.states && section.states.length > 0 && (
            <Badge variant="purple" title={section.states.map((s) => s.query).join('\n')}>
              <Eye className="h-3 w-3" />
              Warunkowa: {describeStateQuery(section.states[0].query)}
            </Badge>
          )}
        </div>

        {mode === 'interactive' && isRepeating && onChangeRepeatingCount && (
          <Button variant="secondary" size="xs" onClick={() => onChangeRepeatingCount(1)}>
            <Plus className="h-3.5 w-3.5" />
            Dodaj wpis
          </Button>
        )}
      </div>

      <div className="space-y-3">
        {rowIndices.map((repIdx) => (
          <div
            key={repIdx ?? 'single'}
            className={`space-y-3 ${
              repIdx !== undefined ? 'rounded-xl border border-slate-200 bg-white p-4' : ''
            }`}
          >
            {repIdx !== undefined && (
              <div className="flex items-center justify-between border-b border-slate-100 pb-2 text-sm font-medium text-slate-700">
                <span>Pozycja #{repIdx + 1}</span>
                {repeatingCount > 1 && onChangeRepeatingCount && (
                  <button
                    type="button"
                    onClick={() => onChangeRepeatingCount(-1)}
                    className="inline-flex items-center gap-1 text-xs font-medium text-rose-600 hover:underline"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
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

              if (mode === 'interactive' && !evalState.visible) {
                return null;
              }

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
