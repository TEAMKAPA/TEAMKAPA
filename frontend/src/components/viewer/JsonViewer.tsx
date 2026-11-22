import { useMemo, useState } from 'react';
import type { FormDefinition } from '../../types/coiForm';
import type { Issue, QualityReport } from '../../types/auditReport';
import { sanitizeFormForExport } from '../../lib/zodSchemaBuilder';
import { downloadFormJson } from '../../api/forms';

interface JsonViewerProps {
  form: FormDefinition;
  report: QualityReport;
  liveRuleIssues: Issue[];
  filename?: string;
}

export function JsonViewer({ form, report, liveRuleIssues, filename }: JsonViewerProps) {
  const [viewMode, setViewMode] = useState<'form' | 'report'>('form');
  const [copied, setCopied] = useState(false);

  const errorCount = liveRuleIssues.filter((i) => i.severity === 'ERROR').length;

  const formatted = useMemo(() => {
    const payload = viewMode === 'form' ? sanitizeFormForExport(form) : report;
    return JSON.stringify(payload, null, 2);
  }, [form, report, viewMode]);

  const lines = useMemo(() => formatted.split('\n'), [formatted]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(formatted);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Brak dostępu do schowka
    }
  };

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#E5E7EB] px-6 py-4">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-[22px] font-normal leading-7 text-[#0D0E0E]">Podgląd JSON</h2>
          {viewMode === 'form' && (
            <span className={`text-xs font-normal ${errorCount === 0 ? 'text-[#065F46]' : 'text-[#CA0049]'}`}>
              {errorCount === 0 ? 'Zgodny z COI' : `Błędy reguł: ${errorCount}`}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setViewMode('form')}
            className={`rounded-lg border px-3 py-1.5 text-sm font-normal ${
              viewMode === 'form'
                ? 'border-[#08255E] bg-[#08255E] text-white'
                : 'border-[#E5E7EB] bg-[#FFFFFF] text-[#0D0E0E]'
            }`}
          >
            Formularz
          </button>
          <button
            type="button"
            onClick={() => setViewMode('report')}
            className={`rounded-lg border px-3 py-1.5 text-sm font-normal ${
              viewMode === 'report'
                ? 'border-[#08255E] bg-[#08255E] text-white'
                : 'border-[#E5E7EB] bg-[#FFFFFF] text-[#0D0E0E]'
            }`}
          >
            Raport
          </button>
          <button
            type="button"
            onClick={handleCopy}
            className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-3 py-1.5 text-sm font-normal text-[#0D0E0E] hover:bg-[#F3F3F4]"
          >
            {copied ? 'Skopiowano' : 'Kopiuj'}
          </button>
          <button
            type="button"
            onClick={() => downloadFormJson(form, filename)}
            className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-3 py-1.5 text-sm font-normal text-[#08255E] hover:bg-[#F3F3F4]"
          >
            Pobierz .json
          </button>
        </div>
      </div>

      <div
        data-testid="json-viewer-code"
        className="thin-scrollbar max-h-[60vh] overflow-auto bg-[#04122F] p-5 font-mono text-[13px] font-normal leading-6 text-white"
      >
        {lines.map((line, index) => (
          <div key={index} className="flex gap-4">
            <span className="w-8 shrink-0 select-none text-right text-white/40 tabular-nums">
              {index + 1}
            </span>
            <span className="whitespace-pre text-white/90">{line}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
