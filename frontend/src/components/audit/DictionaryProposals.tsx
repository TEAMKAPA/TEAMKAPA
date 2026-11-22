import type { DictionaryProposal } from '../../types/auditReport';

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
    <div>
      <h3 className="border-b border-[#E5E7EB] pb-2 text-[18px] font-normal text-[#0D0E0E]">
        Propozycje słowników ({proposals.length})
      </h3>

      {proposals.length === 0 ? (
        <p className="py-4 text-sm font-normal text-[#52555A]">
          Brak propozycji słowników dla tego formularza.
        </p>
      ) : (
        <ul className="divide-y divide-[#E5E7EB]">
          {proposals.map((proposal, idx) => {
            const applied = appliedTechNames?.has(proposal.tech_name) ?? false;
            return (
              <li key={`${proposal.tech_name}-${proposal.name}-${idx}`} className="py-4">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="space-y-1">
                    <div className="text-[15px] font-normal text-[#0D0E0E]">
                      {proposal.name} ({proposal.tech_name})
                    </div>
                    {proposal.usage && (
                      <p className="text-sm font-normal text-[#52555A]">{proposal.usage}</p>
                    )}
                  </div>
                  {onApplyProposal && (
                    <button
                      type="button"
                      disabled={applied}
                      onClick={() => onApplyProposal(proposal)}
                      className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-3 py-1.5 text-sm font-normal text-[#08255E] hover:bg-[#F3F3F4] disabled:opacity-50"
                    >
                      {applied ? 'Zastosowano' : 'Zastosuj słownik'}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
