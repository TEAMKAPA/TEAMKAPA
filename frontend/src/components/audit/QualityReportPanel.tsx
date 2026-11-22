import {
  STAGE_LABELS,
  type DictionaryProposal,
  type FieldSource,
  type Issue,
  type QualityReport,
} from '../../types/auditReport';
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

export function QualityReportPanel({
  report,
  liveRuleIssues,
  appliedDictionaryTechNames,
  onSelectField,
  onFocusSource,
  onApplyDictionaryProposal,
}: QualityReportPanelProps) {
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

  return (
    <div className="space-y-6 p-6">
      <div className="grid grid-cols-2 divide-y divide-[#E5E7EB] border-b border-[#E5E7EB] pb-4 sm:grid-cols-4 sm:divide-x sm:divide-y-0">
        <div className="py-2 sm:pr-4">
          <div className="text-xs font-normal text-[#52555A]">Średnia pewność</div>
          <div className="mt-1 text-[22px] font-normal text-[#0D0E0E] tabular-nums">
            {avgConfidence}%
          </div>
        </div>
        <div className="py-2 sm:px-4">
          <div className="text-xs font-normal text-[#52555A]">Do sprawdzenia</div>
          <div className="mt-1 text-[22px] font-normal text-[#0D0E0E] tabular-nums">
            {mediumCount + lowCount}
          </div>
        </div>
        <div className="py-2 sm:px-4">
          <div className="text-xs font-normal text-[#52555A]">Niska pewność</div>
          <div className="mt-1 text-[22px] font-normal text-[#CA0049] tabular-nums">
            {lowCount}
          </div>
        </div>
        <div className="py-2 sm:pl-4">
          <div className="text-xs font-normal text-[#52555A]">Wszystkie pola</div>
          <div className="mt-1 text-[22px] font-normal text-[#0D0E0E] tabular-nums">
            {report.fields.length}
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

      <div>
        <h3 className="border-b border-[#E5E7EB] pb-2 text-[18px] font-normal text-[#0D0E0E]">
          Etapy analizy ({report.stages.length})
        </h3>
        <div className="divide-y divide-[#E5E7EB]">
          {report.stages.map((stage, idx) => (
            <div
              key={`${stage.name}-${idx}`}
              className="flex flex-wrap items-center justify-between gap-3 py-2.5 text-sm font-normal"
            >
              <span className="text-[#0D0E0E]">{STAGE_LABELS[stage.name] ?? stage.name}</span>
              <span className="text-xs text-[#52555A] tabular-nums">
                {stage.duration_seconds.toFixed(2)} s · {stage.status}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
