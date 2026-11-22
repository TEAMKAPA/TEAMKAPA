import { useMemo, useState } from 'react';
import { getFieldTitle, type FormDefinition, type LeafField } from '../../types/coiForm';
import type { FieldAssessment, Issue } from '../../types/auditReport';
import { evaluateStates, makeAnswerKey, type FormAnswers } from '../../lib/conditionEvaluator';
import { buildFormZodSchema } from '../../lib/zodSchemaBuilder';
import { FieldEditorDrawer } from './FieldEditorDrawer';
import { SectionCard } from './SectionCard';

export type ReviewFilter = 'ALL' | 'NEEDS_REVIEW' | 'LOW' | 'WITH_ISSUES';

interface FormRendererProps {
  mode: 'review' | 'interactive';
  form: FormDefinition;
  assessments: FieldAssessment[];
  liveRuleIssues: Issue[];
  selectedFieldId: string | null;
  activeTabId: string;
  onActiveTabChange: (tabId: string) => void;
  reviewedIds: ReadonlySet<string>;
  modifiedIds: ReadonlySet<string>;
  onSelectField: (fieldId: string | null) => void;
  onUpdateField: (updated: LeafField) => void;
  onResetField: (fieldId: string) => void;
  onToggleReviewed: (fieldId: string) => void;
  onFocusSelectedOnDocument: () => void;
}

export function FormRenderer({
  mode,
  form,
  assessments,
  liveRuleIssues,
  selectedFieldId,
  activeTabId,
  onActiveTabChange,
  reviewedIds,
  modifiedIds,
  onSelectField,
  onUpdateField,
  onResetField,
  onToggleReviewed,
  onFocusSelectedOnDocument,
}: FormRendererProps) {
  const [filter, setFilter] = useState<ReviewFilter>('ALL');
  const [drawerFieldId, setDrawerFieldId] = useState<string | null>(null);

  const [answers, setAnswers] = useState<FormAnswers>({});
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
  const [interactiveValid, setInteractiveValid] = useState<boolean | null>(null);
  const [repeatingCounts, setRepeatingCounts] = useState<Record<string, number>>({});

  const assessmentsById = useMemo(
    () => new Map(assessments.map((a) => [a.field_id, a])),
    [assessments],
  );

  const liveIssuesByFieldId = useMemo(() => {
    const map = new Map<string, Issue[]>();
    for (const issue of liveRuleIssues) {
      if (!issue.field_id) continue;
      const list = map.get(issue.field_id) ?? [];
      list.push(issue);
      map.set(issue.field_id, list);
    }
    return map;
  }, [liveRuleIssues]);

  const activeTab = form.tabs.find((t) => t.id === activeTabId) ?? form.tabs[0];

  const drawerField: LeafField | null = useMemo(() => {
    if (!drawerFieldId) return null;
    for (const tab of form.tabs) {
      for (const sq of tab.questions) {
        for (const fq of sq.field.repetingSectionGrid) {
          if (fq.field.id === drawerFieldId) return fq.field;
        }
      }
    }
    return null;
  }, [form, drawerFieldId]);

  const filterCounts = useMemo(() => {
    let total = 0;
    let needsReview = 0;
    let low = 0;
    let withIssues = 0;

    for (const tab of form.tabs) {
      for (const sq of tab.questions) {
        for (const fq of sq.field.repetingSectionGrid) {
          total += 1;
          const a = assessmentsById.get(fq.field.id);
          const live = liveIssuesByFieldId.get(fq.field.id) ?? [];
          if (a && a.level !== 'HIGH') needsReview += 1;
          if (a && a.level === 'LOW') low += 1;
          if ((a && a.issues.length > 0) || live.length > 0) withIssues += 1;
        }
      }
    }

    return { total, needsReview, low, withIssues };
  }, [form, assessmentsById, liveIssuesByFieldId]);

  const matchesFilter = (field: LeafField): boolean => {
    const a = assessmentsById.get(field.id);
    const live = liveIssuesByFieldId.get(field.id) ?? [];
    if (filter === 'NEEDS_REVIEW' && (!a || a.level === 'HIGH')) return false;
    if (filter === 'LOW' && (!a || a.level !== 'LOW')) return false;
    if (filter === 'WITH_ISSUES' && (!a || a.issues.length === 0) && live.length === 0) return false;
    return Boolean(getFieldTitle(field));
  };

  const handleValidateInteractive = () => {
    const visibleKeys = new Set<string>();
    for (const tab of form.tabs) {
      for (const sq of tab.questions) {
        const secState = evaluateStates(sq.field.states, answers, false);
        if (!secState.visible) continue;
        for (const fq of sq.field.repetingSectionGrid) {
          const leaf = fq.field;
          const leafStates = 'states' in leaf ? leaf.states : undefined;
          const leafEval = evaluateStates(leafStates, answers, false);
          if (leafEval.visible) {
            visibleKeys.add(makeAnswerKey(tab.title, sq.field.techName, leaf.techName));
          }
        }
      }
    }

    const schema = buildFormZodSchema(form, visibleKeys);
    const parsed = schema.safeParse(answers);
    if (parsed.success) {
      setValidationErrors({});
      setInteractiveValid(true);
    } else {
      const errs: Record<string, string> = {};
      for (const issue of parsed.error.issues) {
        const key = String(issue.path[0] ?? '');
        if (key && !errs[key]) errs[key] = issue.message;
      }
      setValidationErrors(errs);
      setInteractiveValid(false);
    }
  };

  return (
    <div>
      {/* Nagłówek + pojedynczy selektor filtrów oraz tekstowe zakładki stron */}
      <div className="border-b border-[#E5E7EB] px-4 pt-4 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-[20px] font-normal leading-7 text-[#0D0E0E]">
            {mode === 'review' ? 'Rozpoznane pola' : 'Podgląd wypełniania'}
          </h2>

          {mode === 'review' && (
            <label className="flex items-center gap-2 text-sm font-normal text-[#52555A]">
              <span>Pokaż:</span>
              <select
                aria-label="Filtr rozpoznanych pól"
                data-testid="review-filter-select"
                value={filter}
                onChange={(e) => setFilter(e.target.value as ReviewFilter)}
                className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-3 py-1.5 text-sm font-normal text-[#0D0E0E] shadow-none focus:border-[#08255E] focus:outline-none"
              >
                <option value="ALL">Wszystkie ({filterCounts.total})</option>
                <option value="NEEDS_REVIEW">Do sprawdzenia ({filterCounts.needsReview})</option>
                <option value="LOW">Niska pewność ({filterCounts.low})</option>
                <option value="WITH_ISSUES">Z uwagami ({filterCounts.withIssues})</option>
              </select>
            </label>
          )}
        </div>

        {/* Tekstowe zakładki stron formularza w jednym rzędzie */}
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1">
          {form.tabs.map((tab, idx) => {
            const isActive = activeTab?.id === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => onActiveTabChange(tab.id)}
                className={`-mb-px whitespace-nowrap border-b-2 py-2.5 text-[15px] font-normal transition-colors ${
                  isActive
                    ? 'border-[#08255E] text-[#08255E]'
                    : 'border-transparent text-[#374151] hover:text-[#0D0E0E]'
                }`}
              >
                {idx + 1}. {tab.title}
              </button>
            );
          })}
        </div>

        {mode === 'interactive' && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[#E5E7EB] py-3">
            <div className="text-sm font-normal text-[#52555A]">
              {interactiveValid === true && (
                <span className="text-[#065F46]">Wszystkie widoczne pola są poprawne.</span>
              )}
              {interactiveValid === false && (
                <span className="text-[#CA0049]">
                  Wykryto błędy wypełnienia ({Object.keys(validationErrors).length}).
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  setAnswers({});
                  setValidationErrors({});
                  setInteractiveValid(null);
                }}
                className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-3 py-1.5 text-sm font-normal text-[#0D0E0E] hover:bg-[#F3F3F4]"
              >
                Wyczyść
              </button>
              <button
                type="button"
                onClick={handleValidateInteractive}
                className="rounded-lg bg-[#08255E] px-4 py-1.5 text-sm font-normal text-white hover:bg-[#04122F]"
              >
                Sprawdź walidację formularza
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Lista sekcji i pól */}
      {activeTab && (
        <div className="space-y-5 p-4 sm:px-6 sm:py-5">
          {activeTab.questions.map((sq) => {
            const visibleQuestions =
              mode === 'review'
                ? sq.field.repetingSectionGrid.filter((fq) => matchesFilter(fq.field))
                : sq.field.repetingSectionGrid;

            return (
              <SectionCard
                key={sq.field.id}
                tabTitle={activeTab.title}
                section={sq.field}
                visibleQuestions={visibleQuestions}
                mode={mode}
                assessmentsById={assessmentsById}
                liveIssuesByFieldId={liveIssuesByFieldId}
                selectedFieldId={selectedFieldId}
                reviewedIds={reviewedIds}
                modifiedIds={modifiedIds}
                onSelectField={(id) => onSelectField(id)}
                onOpenDetails={(id) => {
                  onSelectField(id);
                  setDrawerFieldId(id);
                }}
                onUpdateField={onUpdateField}
                onToggleReviewed={onToggleReviewed}
                answers={answers}
                onAnswerChange={(key, value) => {
                  setAnswers((prev) => ({ ...prev, [key]: value }));
                  if (validationErrors[key]) {
                    setValidationErrors((prev) => {
                      const next = { ...prev };
                      delete next[key];
                      return next;
                    });
                  }
                }}
                validationErrors={validationErrors}
                repeatingCount={repeatingCounts[sq.field.id] ?? 1}
                onChangeRepeatingCount={(delta) =>
                  setRepeatingCounts((prev) => ({
                    ...prev,
                    [sq.field.id]: Math.max(1, (prev[sq.field.id] ?? 1) + delta),
                  }))
                }
              />
            );
          })}

          {mode === 'review' &&
            activeTab.questions.every(
              (sq) =>
                sq.field.repetingSectionGrid.filter((fq) => matchesFilter(fq.field)).length === 0,
            ) && (
              <div className="rounded-lg border border-[#E5E7EB] bg-[#F3F3F4] p-8 text-center text-sm font-normal text-[#52555A]">
                <p>Brak pól na stronie „{activeTab.title}” dla wybranego filtra.</p>
                {filter !== 'ALL' && (
                  <button
                    type="button"
                    onClick={() => setFilter('ALL')}
                    className="mt-3 rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-4 py-1.5 text-sm font-normal text-[#0D0E0E] hover:bg-[#F3F3F4]"
                  >
                    Pokaż wszystkie pola
                  </button>
                )}
              </div>
            )}
        </div>
      )}

      {mode === 'review' && drawerField && (
        <FieldEditorDrawer
          field={drawerField}
          assessment={assessmentsById.get(drawerField.id)}
          liveIssues={liveIssuesByFieldId.get(drawerField.id) ?? []}
          isReviewed={reviewedIds.has(drawerField.id)}
          isModified={modifiedIds.has(drawerField.id)}
          onUpdateField={onUpdateField}
          onResetField={onResetField}
          onToggleReviewed={onToggleReviewed}
          onFocusOnDocument={onFocusSelectedOnDocument}
          onClose={() => setDrawerFieldId(null)}
        />
      )}
    </div>
  );
}
