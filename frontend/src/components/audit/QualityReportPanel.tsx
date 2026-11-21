import {
  AlertTriangle,
  CheckCircle2,
  Coins,
  Cpu,
  Layers,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react';
import {
  STAGE_LABELS,
  type DictionaryProposal,
  type FieldSource,
  type Issue,
  type QualityReport,
  type StageStatus,
} from '../../types/auditReport';
import { Badge, type BadgeVariant } from '../ui/Badge';
import { Card, CardHeader } from '../ui/Card';
import { DictionaryProposals } from './DictionaryProposals';
import { MissedFieldsPanel } from './MissedFieldsPanel';

interface QualityReportPanelProps {
  report: QualityReport;
  liveRuleIssues: Issue[];
  appliedDictionaryTechNames?: ReadonlySet<string>;
  onSelectField: (fieldId: string) => void;
  onFocusSource: (source: FieldSource, label?: string) => void;
  onApplyDictionaryProposal?: (proposal: DictionaryProposal) => void;
}

const STAGE_BADGE: Record<StageStatus, BadgeVariant> = {
  OK: 'success',
  SKIPPED: 'neutral',
  DEGRADED: 'warning',
};

export function QualityReportPanel({
  report,
  liveRuleIssues,
  appliedDictionaryTechNames,
  onSelectField,
  onFocusSource,
  onApplyDictionaryProposal,
}: QualityReportPanelProps) {
  const highCount = report.fields.filter((f) => f.level === 'HIGH').length;
  const mediumCount = report.fields.filter((f) => f.level === 'MEDIUM').length;
  const lowCount = report.fields.filter((f) => f.level === 'LOW').length;
  const avgConfidence =
    report.fields.length > 0
      ? Math.round(
          (report.fields.reduce((acc, f) => acc + f.confidence, 0) / report.fields.length) * 100,
        )
      : 0;

  const combinedIssues: Issue[] = [
    ...liveRuleIssues,
    ...report.issues,
    ...report.fields.flatMap((f) => f.issues),
  ];
  const uniqueIssues = combinedIssues.filter(
    (issue, idx, arr) =>
      arr.findIndex(
        (other) =>
          other.code === issue.code &&
          other.field_id === issue.field_id &&
          other.message === issue.message,
      ) === idx,
  );

  const errorCount = uniqueIssues.filter((i) => i.severity === 'ERROR').length;
  const warningCount = uniqueIssues.filter((i) => i.severity === 'WARNING').length;

  return (
    <div className="space-y-5">
      {report.demo_mode && (
        <div className="flex items-start gap-3 rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
          <div>
            <div className="font-semibold">Wynik wygenerowany w trybie demonstracyjnym backendu</div>
            <p className="mt-0.5 text-amber-900">
              Pipeline pracował bez modelu językowego (<code>LLM_DEMO_MODE=true</code>). Wszystkie
              pola otrzymały niską pewność i wymagają weryfikacji przed importem do COI.
            </p>
          </div>
        </div>
      )}

      {/* Karty podsumowania KPI */}
      <div className="grid grid-cols-2 gap-3.5 sm:grid-cols-4">
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-2xs">
          <div className="text-xs font-medium text-slate-500">Średnia pewność</div>
          <div className="mt-1.5 text-2xl font-bold text-slate-900 tabular-nums">
            {avgConfidence}%
          </div>
          <div className="mt-2 flex flex-wrap gap-x-2.5 gap-y-0.5 text-xs font-medium">
            <span className="text-emerald-700">{highCount} pewne</span>
            <span className="text-amber-700">{mediumCount} śr.</span>
            <span className="text-rose-700">{lowCount} niskie</span>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-2xs">
          <div className="text-xs font-medium text-slate-500">Wymaga przeglądu</div>
          <div className="mt-1.5 text-2xl font-bold text-amber-700 tabular-nums">
            {mediumCount + lowCount}
          </div>
          <div className="mt-2 text-xs text-slate-500">z {report.fields.length} pól ogółem</div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-2xs">
          <div className="text-xs font-medium text-slate-500">Zgodność reguł COI</div>
          <div className="mt-1.5 flex items-center gap-1.5 text-2xl font-bold">
            {errorCount === 0 ? (
              <span className="inline-flex items-center gap-1.5 text-emerald-700">
                <ShieldCheck className="h-6 w-6" />
                Poprawny
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-rose-700">
                <ShieldAlert className="h-6 w-6" />
                {errorCount} błędów
              </span>
            )}
          </div>
          <div className="mt-2 text-xs text-slate-500">Ostrzeżeń: {warningCount}</div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-2xs">
          <div className="text-xs font-medium text-slate-500">Tryb dokładności</div>
          <div className="mt-1.5 text-2xl font-bold uppercase text-slate-900">
            {report.accuracy_mode}
          </div>
          <div className="mt-2 text-xs text-slate-500">
            Iteracje naprawcze: {report.repair_iterations}
          </div>
        </div>
      </div>

      <MissedFieldsPanel
        skippedElements={report.skipped_elements}
        issues={uniqueIssues}
        onSelectField={onSelectField}
        onFocusSource={onFocusSource}
      />

      <DictionaryProposals
        proposals={report.dictionary_proposals}
        appliedTechNames={appliedDictionaryTechNames}
        onApplyProposal={onApplyDictionaryProposal}
      />

      {/* Przebieg etapów analizy */}
      <Card>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-2">
              <Layers className="h-5 w-5 text-blue-600" />
              Przebieg etapów analizy ({report.stages.length})
            </span>
          }
          subtitle={
            report.document_type
              ? `Rozpoznany typ dokumentu: ${report.document_type}`
              : 'Statusy i czasy poszczególnych etapów pipeline’u'
          }
        />
        <div className="divide-y divide-slate-100">
          {report.stages.map((stage, idx) => (
            <div
              key={`${stage.name}-${idx}`}
              className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 text-sm"
            >
              <div className="flex items-center gap-2.5">
                <CheckCircle2
                  className={`h-4 w-4 shrink-0 ${
                    stage.status === 'OK'
                      ? 'text-emerald-600'
                      : stage.status === 'DEGRADED'
                        ? 'text-amber-500'
                        : 'text-slate-400'
                  }`}
                />
                <span className="font-medium text-slate-900">
                  {STAGE_LABELS[stage.name] ?? stage.name}
                </span>
                {stage.detail && <span className="text-xs text-slate-500">({stage.detail})</span>}
              </div>
              <div className="flex items-center gap-3">
                <span className="font-mono text-xs text-slate-500 tabular-nums">
                  {stage.duration_seconds.toFixed(2)} s
                </span>
                <Badge variant={STAGE_BADGE[stage.status]}>{stage.status}</Badge>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* Modele i koszt */}
      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader
            title={
              <span className="inline-flex items-center gap-2">
                <Cpu className="h-4 w-4 text-slate-600" />
                Użyte modele (otwarte wagi)
              </span>
            }
          />
          <dl className="divide-y divide-slate-100 px-5 py-2 text-sm">
            {Object.entries(report.models).map(([role, modelName]) => (
              <div key={role} className="flex items-center justify-between gap-3 py-2.5">
                <dt className="text-slate-500">{role}</dt>
                <dd className="truncate font-mono text-xs text-slate-800" title={modelName}>
                  {modelName}
                </dd>
              </div>
            ))}
          </dl>
        </Card>

        <Card>
          <CardHeader
            title={
              <span className="inline-flex items-center gap-2">
                <Coins className="h-4 w-4 text-slate-600" />
                Zużycie i szacowany koszt
              </span>
            }
          />
          <dl className="divide-y divide-slate-100 px-5 py-2 text-sm">
            {Object.keys(report.usage).length === 0 ? (
              <div className="py-5 text-center text-slate-500">Brak danych o zużyciu LLM.</div>
            ) : (
              Object.entries(report.usage).map(([key, val]) => (
                <div key={key} className="flex items-center justify-between gap-3 py-2.5">
                  <dt className="text-slate-500">{key}</dt>
                  <dd className="font-mono font-semibold text-slate-900 tabular-nums">
                    {key === 'cost_usd' ? `$${val.toFixed(4)}` : val.toLocaleString('pl-PL')}
                  </dd>
                </div>
              ))
            )}
          </dl>
        </Card>
      </div>
    </div>
  );
}
