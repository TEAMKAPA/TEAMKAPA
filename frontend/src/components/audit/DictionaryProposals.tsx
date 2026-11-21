import { BookOpen, Check } from 'lucide-react';
import type { DictionaryProposal } from '../../types/auditReport';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card, CardHeader } from '../ui/Card';

interface DictionaryProposalsProps {
  proposals: DictionaryProposal[];
  appliedTechNames?: ReadonlySet<string>;
  onApplyProposal?: (proposal: DictionaryProposal) => void;
}

export function DictionaryProposals({
  proposals,
  appliedTechNames,
  onApplyProposal,
}: DictionaryProposalsProps) {
  return (
    <Card>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <BookOpen className="h-5 w-5 text-indigo-600" />
            Propozycje słowników COI ({proposals.length})
          </span>
        }
        subtitle="Rekomendowane słowniki dla pól z zamkniętą listą wartości"
      />
      <div className="p-5">
        {proposals.length === 0 ? (
          <p className="py-4 text-center text-sm text-slate-500">
            Brak nowych propozycji słowników dla tego formularza.
          </p>
        ) : (
          <ul className="space-y-3">
            {proposals.map((proposal, idx) => {
              const applied = appliedTechNames?.has(proposal.tech_name) ?? false;
              return (
                <li
                  key={`${proposal.tech_name}-${proposal.name}-${idx}`}
                  className="rounded-xl border border-indigo-200/80 bg-indigo-50/40 p-4 text-sm"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-slate-900">{proposal.name}</span>
                        <Badge variant="purple">
                          pole: <code className="font-mono">{proposal.tech_name}</code>
                        </Badge>
                      </div>
                      {proposal.usage && <p className="text-slate-600">{proposal.usage}</p>}
                    </div>
                    {onApplyProposal && (
                      <Button
                        variant={applied ? 'success' : 'primary'}
                        size="xs"
                        disabled={applied}
                        onClick={() => onApplyProposal(proposal)}
                      >
                        <Check className="h-3.5 w-3.5" />
                        {applied ? 'Zastosowano' : 'Zastosuj słownik'}
                      </Button>
                    )}
                  </div>

                  {proposal.entries.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {proposal.entries.slice(0, 12).map((entry) => (
                        <span
                          key={entry.code}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1 text-xs text-slate-800 ring-1 ring-indigo-100"
                        >
                          <code className="font-mono text-indigo-600">{entry.code}</code>
                          <span>{entry.label}</span>
                        </span>
                      ))}
                      {proposal.entries.length > 12 && (
                        <span className="self-center text-xs text-slate-500">
                          +{proposal.entries.length - 12} więcej
                        </span>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}
