import { useMemo, useState } from 'react';
import { Check, Copy, Download, Search, ShieldAlert, ShieldCheck } from 'lucide-react';
import type { FormDefinition } from '../../types/coiForm';
import type { Issue, QualityReport } from '../../types/auditReport';
import { sanitizeFormForExport } from '../../lib/zodSchemaBuilder';
import { downloadFormJson } from '../../api/forms';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

interface JsonViewerProps {
  form: FormDefinition;
  report: QualityReport;
  liveRuleIssues: Issue[];
  filename?: string;
}

function renderJsonLine(line: string) {
  const keyMatch = /^(\s*)("[^"]+")(:\s*)(.*)$/.exec(line);
  if (!keyMatch) {
    return <span className="text-slate-300">{line}</span>;
  }
  const [, indent, key, colon, rest] = keyMatch;
  let valueClass = 'text-slate-200';
  if (rest.startsWith('"')) valueClass = 'text-emerald-300';
  else if (rest.startsWith('true') || rest.startsWith('false')) valueClass = 'text-amber-300';
  else if (/^-?\d/.test(rest)) valueClass = 'text-sky-300';

  return (
    <>
      <span>{indent}</span>
      <span className="text-blue-300">{key}</span>
      <span className="text-slate-400">{colon}</span>
      <span className={valueClass}>{rest}</span>
    </>
  );
}

export function JsonViewer({ form, report, liveRuleIssues, filename }: JsonViewerProps) {
  const [viewMode, setViewMode] = useState<'form' | 'report'>('form');
  const [search, setSearch] = useState('');
  const [copied, setCopied] = useState(false);

  const errorCount = liveRuleIssues.filter((i) => i.severity === 'ERROR').length;
  const warningCount = liveRuleIssues.filter((i) => i.severity === 'WARNING').length;

  const formatted = useMemo(() => {
    const payload = viewMode === 'form' ? sanitizeFormForExport(form) : report;
    return JSON.stringify(payload, null, 2);
  }, [form, report, viewMode]);

  const lines = useMemo(() => formatted.split('\n'), [formatted]);
  const query = search.trim().toLowerCase();

  const matchingLineCount = useMemo(() => {
    if (!query) return 0;
    return lines.filter((line) => line.toLowerCase().includes(query)).length;
  }, [lines, query]);

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
    <div className="flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xs">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="inline-flex rounded-xl border border-slate-200 bg-slate-100 p-1 text-xs">
            <button
              type="button"
              onClick={() => setViewMode('form')}
              className={`rounded-lg px-3 py-1.5 font-semibold transition-colors ${
                viewMode === 'form'
                  ? 'bg-white text-slate-900 shadow-2xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Plik importu COI
            </button>
            <button
              type="button"
              onClick={() => setViewMode('report')}
              className={`rounded-lg px-3 py-1.5 font-semibold transition-colors ${
                viewMode === 'report'
                  ? 'bg-white text-slate-900 shadow-2xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Raport jakości
            </button>
          </div>

          {viewMode === 'form' &&
            (errorCount === 0 ? (
              <Badge variant="success">
                <ShieldCheck className="h-3.5 w-3.5" />
                Zgodny ze schematem COI{warningCount > 0 ? ` (${warningCount} ostrz.)` : ''}
              </Badge>
            ) : (
              <Badge variant="danger">
                <ShieldAlert className="h-3.5 w-3.5" />
                {errorCount} błędów reguł COI
              </Badge>
            ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Szukaj w JSON…"
              className="h-8 w-44 rounded-lg border border-slate-300 bg-white pr-3 pl-8 text-xs focus:border-blue-600 focus:outline-none sm:w-52"
            />
          </div>

          <Button variant="secondary" size="xs" onClick={handleCopy}>
            {copied ? (
              <Check className="h-3.5 w-3.5 text-emerald-600" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
            {copied ? 'Skopiowano' : 'Kopiuj'}
          </Button>

          <Button
            variant="primary"
            size="xs"
            onClick={() => downloadFormJson(form, filename)}
            title="Pobierz czysty plik JSON gotowy do importu w COI"
          >
            <Download className="h-3.5 w-3.5" />
            Pobierz COI JSON
          </Button>
        </div>
      </div>

      {query && (
        <div className="border-b border-slate-200 bg-blue-50 px-4 py-2 text-xs text-blue-900">
          Znaleziono pasujących linii: <strong>{matchingLineCount}</strong>
        </div>
      )}

      <div
        data-testid="json-viewer-code"
        className="thin-scrollbar max-h-[65vh] overflow-auto bg-slate-900 p-4 font-mono text-[13px] leading-6 text-slate-100"
      >
        {lines.map((line, index) => {
          const isMatch = Boolean(query && line.toLowerCase().includes(query));
          return (
            <div
              key={index}
              className={`flex gap-4 px-1.5 ${isMatch ? 'rounded bg-amber-400/20' : ''}`}
            >
              <span className="w-9 shrink-0 select-none text-right text-slate-500 tabular-nums">
                {index + 1}
              </span>
              <span className="whitespace-pre">{renderJsonLine(line)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
