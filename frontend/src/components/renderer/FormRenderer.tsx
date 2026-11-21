import { useMemo, useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  ClipboardCheck,
  RotateCcw,
  Search,
} from 'lucide-react';
import { getFieldTitle, type FormDefinition, type LeafField } from '../../types/coiForm';
import type { FieldAssessment, Issue } from '../../types/auditReport';
import { evaluateStates, makeAnswerKey, type FormAnswers } from '../../lib/conditionEvaluator';
import { buildFormZodSchema } from '../../lib/zodSchemaBuilder';
import { Button } from '../ui/Button';
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
  const [search, setSearch] = useState('');

  // Stan interaktywnego podglądu wypełniania
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

  const selectedField: LeafField | null = useMemo(() => {
    if (!selectedFieldId) return null;
    for (const tab of form.tabs) {
      for (const sq of tab.questions) {
        for (const fq of sq.field.repetingSectionGrid) {
          if (fq.field.id === selectedFieldId) return fq.field;
        }
      }
    }
    return null;
  }, [form, selectedFieldId]);

  // Lista niepewnych pól do prowadzenia urzędnika krok po kroku
  const unreviewedUncertainIds = useMemo(() => {
    const ids: string[] = [];
    for (const tab of form.tabs) {
      for (const sq of tab.questions) {
        for (const fq of sq.field.repetingSectionGrid) {
          const a = assessmentsById.get(fq.field.id);
          if (a && a.level !== 'HIGH' && !reviewedIds.has(fq.field.id)) {
            ids.push(fq.field.id);
          }
        }
      }
    }
    return ids;
  }, [form, assessmentsById, reviewedIds]);

  const filterCounts = useMemo(() => {
    let total = 0;
    let needsReview = 0;
    let low = 0;
    let withIssues = 0;
    let reviewedUncertain = 0;

    for (const tab of form.tabs) {
      for (const sq of tab.questions) {
        for (const fq of sq.field.repetingSectionGrid) {
          total += 1;
          const a = assessmentsById.get(fq.field.id);
          const live = liveIssuesByFieldId.get(fq.field.id) ?? [];
          if (a && a.level !== 'HIGH') {
            needsReview += 1;
            if (reviewedIds.has(fq.field.id)) reviewedUncertain += 1;
          }
          if (a && a.level === 'LOW') low += 1;
          if ((a && a.issues.length > 0) || live.length > 0) withIssues += 1;
        }
      }
    }

    return { total, needsReview, low, withIssues, reviewedUncertain };
  }, [form, assessmentsById, liveIssuesByFieldId, reviewedIds]);

  const matchesFilter = (field: LeafField): boolean => {
    const a = assessmentsById.get(field.id);
    const live = liveIssuesByFieldId.get(field.id) ?? [];
    if (filter === 'NEEDS_REVIEW' && (!a || a.level === 'HIGH')) return false;
    if (filter === 'LOW' && (!a || a.level !== 'LOW')) return false;
    if (filter === 'WITH_ISSUES' && (!a || a.issues.length === 0) && live.length === 0) return false;

    const q = search.trim().toLowerCase();
    if (q) {
      const title = getFieldTitle(field).toLowerCase();
      const tech = field.techName.toLowerCase();
      if (!title.includes(q) && !tech.includes(q)) return false;
    }
    return true;
  };

  const handleJumpToNextUncertain = () => {
    if (unreviewedUncertainIds.length === 0) return;
    const currentIdx = selectedFieldId ? unreviewedUncertainIds.indexOf(selectedFieldId) : -1;
    const nextId = unreviewedUncertainIds[(currentIdx + 1) % unreviewedUncertainIds.length];
    onSelectField(nextId);
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
        if (key && !errs[key]) {
          errs[key] = issue.message;
        }
      }
      setValidationErrors(errs);
      setInteractiveValid(false);
    }
  };

  const reviewProgressPct =
    filterCounts.needsReview > 0
      ? Math.round((filterCounts.reviewedUncertain / filterCounts.needsReview) * 100)
      : 100;

  return (
    <div className="space-y-4">
      {/* Nawigacja po stronach formularza (tabs) + pojedynczy pasek narzędzi */}
      <div className="rounded-2xl border border-slate-200/90 bg-white shadow-xs">
        <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-100 px-4 py-3">
          {form.tabs.map((tab, idx) => {
            const isActive = activeTab?.id === tab.id;
            const uncertainInTab = tab.questions
              .flatMap((sq) => sq.field.repetingSectionGrid)
              .filter((fq) => {
                const a = assessmentsById.get(fq.field.id);
                return a && a.level !== 'HIGH' && !reviewedIds.has(fq.field.id);
              }).length;

            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => onActiveTabChange(tab.id)}
                className={`inline-flex items-center gap-2 rounded-xl px-3.5 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-blue-700 text-white shadow-2xs'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                }`}
              >
                <span className={`text-xs ${isActive ? 'text-blue-200' : 'text-slate-400'}`}>
                  {idx + 1}.
                </span>
                <span>{tab.title}</span>
                {tab.type === 'FINISH' && (
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                      isActive ? 'bg-blue-800 text-blue-100' : 'bg-slate-100 text-slate-500'
                    }`}
                  >
                    Koniec
                  </span>
                )}
                {uncertainInTab > 0 && (
                  <span
                    title={`${uncertainInTab} pól do sprawdzenia na tej stronie`}
                    className={`rounded-full px-2 py-0.5 text-xs font-bold tabular-nums ${
                      isActive ? 'bg-amber-400 text-slate-950' : 'bg-amber-100 text-amber-900'
                    }`}
                  >
                    {uncertainInTab}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Tryb przeglądu: filtry + postęp weryfikacji + przycisk prowadzący */}
        {mode === 'review' && (
          <div className="space-y-3 px-4 py-3.5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-1.5">
                {(
                  [
                    ['ALL', 'Wszystkie', filterCounts.total],
                    ['NEEDS_REVIEW', 'Do przeglądu', filterCounts.needsReview],
                    ['LOW', 'Niska pewność', filterCounts.low],
                    ['WITH_ISSUES', 'Z uwagami', filterCounts.withIssues],
                  ] as const
                ).map(([key, label, count]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setFilter(key)}
                    data-testid={`filter-${key}`}
                    className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors tabular-nums ${
                      filter === key
                        ? 'bg-slate-900 text-white'
                        : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                    }`}
                  >
                    {label} ({count})
                  </button>
                ))}
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <div className="relative">
                  <Search className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-slate-400" />
                  <input
                    type="search"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Szukaj pola…"
                    className="h-8 w-44 rounded-lg border border-slate-300 bg-white pr-3 pl-8 text-xs focus:border-blue-600 focus:outline-none"
                  />
                </div>
                {unreviewedUncertainIds.length > 0 && (
                  <Button variant="secondary" size="xs" onClick={handleJumpToNextUncertain}>
                    Następne niepewne
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            </div>

            {filterCounts.needsReview > 0 && (
              <div className="flex items-center gap-3 pt-0.5 text-xs text-slate-600">
                <span className="shrink-0 font-medium tabular-nums">
                  Postęp weryfikacji niepewnych pól: {filterCounts.reviewedUncertain} z{' '}
                  {filterCounts.needsReview}
                </span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-emerald-500 transition-all duration-300"
                    style={{ width: `${reviewProgressPct}%` }}
                  />
                </div>
                <span className="shrink-0 font-semibold text-slate-800 tabular-nums">
                  {reviewProgressPct}%
                </span>
              </div>
            )}
          </div>
        )}

        {/* Tryb interaktywnego podglądu wypełniania */}
        {mode === 'interactive' && (
          <div className="space-y-3 px-4 py-3.5">
            <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
              <span className="text-slate-600">
                Przetestuj formularz oczami obywatela — warunki JSONata i polskie walidatory działają na żywo.
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setAnswers({});
                    setValidationErrors({});
                    setInteractiveValid(null);
                  }}
                >
                  <RotateCcw className="h-4 w-4" />
                  Wyczyść
                </Button>
                <Button variant="primary" size="sm" onClick={handleValidateInteractive}>
                  <ClipboardCheck className="h-4 w-4" />
                  Sprawdź walidację formularza
                </Button>
              </div>
            </div>

            {interactiveValid !== null && (
              <div
                className={`flex items-center gap-2.5 rounded-xl border px-4 py-3 text-sm font-medium ${
                  interactiveValid
                    ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
                    : 'border-rose-200 bg-rose-50 text-rose-900'
                }`}
              >
                {interactiveValid ? (
                  <>
                    <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600" />
                    Wszystkie widoczne pola przeszły walidację!
                  </>
                ) : (
                  <>
                    <AlertCircle className="h-5 w-5 shrink-0 text-rose-600" />
                    Wykryto błędy wypełnienia ({Object.keys(validationErrors).length}) — sprawdź
                    pola oznaczone na czerwono.
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Sekcje aktywnej strony */}
      {activeTab && (
        <div className="space-y-4">
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
              <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center">
                <p className="text-sm font-medium text-slate-700">
                  Brak pól na stronie „{activeTab.title}” pasujących do wybranego filtra.
                </p>
                {filter !== 'ALL' && (
                  <div className="mt-3 flex justify-center gap-2">
                    <Button variant="secondary" size="sm" onClick={() => setFilter('ALL')}>
                      Pokaż wszystkie pola
                    </Button>
                    {unreviewedUncertainIds.length > 0 && (
                      <Button variant="primary" size="sm" onClick={handleJumpToNextUncertain}>
                        Przejdź do niepewnego pola na innej stronie
                      </Button>
                    )}
                  </div>
                )}
              </div>
            )}
        </div>
      )}

      {/* Boczny panel edycji (slide-over — nie przesuwa listy pól i zostawia dokument po lewej widoczny) */}
      {mode === 'review' && selectedField && (
        <FieldEditorDrawer
          field={selectedField}
          assessment={assessmentsById.get(selectedField.id)}
          liveIssues={liveIssuesByFieldId.get(selectedField.id) ?? []}
          isReviewed={reviewedIds.has(selectedField.id)}
          isModified={modifiedIds.has(selectedField.id)}
          onUpdateField={onUpdateField}
          onResetField={onResetField}
          onToggleReviewed={onToggleReviewed}
          onFocusOnDocument={onFocusSelectedOnDocument}
          onClose={() => onSelectField(null)}
        />
      )}
    </div>
  );
}
