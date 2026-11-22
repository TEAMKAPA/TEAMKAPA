import {
  type BatchView,
  type JobStatus,
  type JobSummary,
  type JobView,
} from '../../types/auditReport';

interface BatchQueueProps {
  jobs: JobView[];
  activeBatch: BatchView | null;
  statusFilter: JobStatus | 'ALL';
  onStatusFilterChange: (status: JobStatus | 'ALL') => void;
  onSelectJob: (jobId: string) => void;
  onDownloadJobJson: (jobId: string) => void;
  onRefresh: () => void;
  isRefreshing: boolean;
}

function getFileFormat(filename: string): string {
  const idx = filename.lastIndexOf('.');
  if (idx <= 0 || idx === filename.length - 1) return 'Inny';
  return filename.slice(idx + 1).toUpperCase();
}

function getStatusLabel(job: JobView): string {
  if (job.status === 'SUCCEEDED') {
    return job.approved_at ? 'Zatwierdzono' : 'Do weryfikacji';
  }
  if (job.status === 'IN_PROGRESS') {
    if (job.progress) {
      const pct = Math.min(100, Math.round((job.progress.step / job.progress.total_steps) * 100));
      return `W trakcie (${pct}%)`;
    }
    return 'W trakcie';
  }
  if (job.status === 'QUEUED') return 'W kolejce';
  return 'Błąd';
}

function getStatusClass(job: JobView): string {
  if (job.status === 'SUCCEEDED') {
    return job.approved_at ? 'text-[#065F46]' : 'text-[#08255E]';
  }
  if (job.status === 'FAILED') return 'text-[#CA0049]';
  return 'text-[#52555A]';
}

export function BatchQueue({
  jobs,
  activeBatch,
  statusFilter,
  onStatusFilterChange,
  onSelectJob,
  onDownloadJobJson,
  onRefresh,
  isRefreshing,
}: BatchQueueProps) {
  const filteredJobs =
    statusFilter === 'ALL' ? jobs : jobs.filter((job) => job.status === statusFilter);

  return (
    <section
      id="kolejka"
      className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] shadow-none"
    >
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#E5E7EB] px-6 py-4">
        <div>
          <h2 className="text-[22px] font-normal leading-7 text-[#0D0E0E]">
            Lista dokumentów
          </h2>
          {activeBatch && (
            <p className="mt-1 text-sm font-normal text-[#52555A]">
              Ostatnia paczka: {activeBatch.total_files} plików (gotowe:{' '}
              {activeBatch.counts.SUCCEEDED ?? 0}, odrzucone: {activeBatch.rejected.length})
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-wrap items-center gap-1.5">
            {(
              [
                ['ALL', 'Wszystkie'],
                ['IN_PROGRESS', 'W trakcie'],
                ['SUCCEEDED', 'Gotowe'],
                ['FAILED', 'Błędy'],
              ] as const
            ).map(([key, label]) => {
              const active = statusFilter === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => onStatusFilterChange(key)}
                  className={`rounded-lg border px-3 py-1.5 text-sm font-normal transition-colors ${
                    active
                      ? 'border-[#04122F] bg-[#04122F] text-white'
                      : 'border-[#E5E7EB] bg-[#FFFFFF] text-[#0D0E0E] hover:bg-[#F3F3F4]'
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>

          <button
            type="button"
            onClick={onRefresh}
            disabled={isRefreshing}
            className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-3.5 py-1.5 text-sm font-normal text-[#0D0E0E] hover:bg-[#F3F3F4] disabled:opacity-50"
          >
            {isRefreshing ? 'Odświeżanie...' : 'Odśwież'}
          </button>
        </div>
      </div>

      {activeBatch && activeBatch.rejected.length > 0 && (
        <div className="border-b border-[#E5E7EB] px-4 py-3 text-sm font-normal sm:px-6">
          <div className="text-[#CA0049]">
            Odrzucone pliki z paczki ({activeBatch.rejected.length}):
          </div>
          <ul className="mt-1.5 divide-y divide-[#E5E7EB]">
            {activeBatch.rejected.map((rej, idx) => (
              <li
                key={`${rej.filename ?? 'brak'}-${idx}`}
                className="flex flex-wrap items-center justify-between gap-2 py-1.5 text-xs sm:text-sm"
              >
                <span className="text-[#0D0E0E]">{rej.filename ?? '(brak nazwy)'}</span>
                <span className="text-[#CA0049]">{rej.message}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {filteredJobs.length === 0 ? (
        <div className="px-4 py-10 text-center text-sm font-normal text-[#52555A] sm:px-6">
          Brak dokumentów na liście. Prześlij plik powyżej lub załaduj przykład.
        </div>
      ) : (
        <>
          {/* Widok mobilny: czytelne wiersze bez przewijania poziomego */}
          <ul className="divide-y divide-[#E5E7EB] sm:hidden">
            {filteredJobs.map((job) => (
              <li key={job.job_id} className="space-y-2 px-4 py-3.5 text-sm font-normal">
                <div className="flex items-start justify-between gap-3">
                  <span className="min-w-0 break-all text-[#0D0E0E]">{job.filename}</span>
                  <span className="shrink-0 text-xs text-[#52555A]">
                    {getFileFormat(job.filename)}
                  </span>
                </div>
                {job.status === 'FAILED' && job.error && (
                  <div className="text-xs text-[#CA0049]">{job.error.message}</div>
                )}
                <div className="flex items-center justify-between gap-3">
                  <span className={getStatusClass(job)}>{getStatusLabel(job)}</span>
                  {job.status === 'SUCCEEDED' ? (
                    <div className="flex items-center gap-4">
                      <button
                        type="button"
                        onClick={() => onDownloadJobJson(job.job_id)}
                        className="text-sm font-normal text-[#52555A] hover:underline"
                      >
                        JSON
                      </button>
                      <button
                        type="button"
                        onClick={() => onSelectJob(job.job_id)}
                        className="text-sm font-normal text-[#08255E] hover:underline"
                      >
                        Otwórz
                      </button>
                    </div>
                  ) : (
                    <span className="text-xs text-[#9FA1A5]">Oczekiwanie</span>
                  )}
                </div>
              </li>
            ))}
          </ul>

          {/* Widok desktopowy: tabela */}
          <div className="hidden overflow-x-auto sm:block">
            <table className="w-full border-collapse text-left text-[15px] font-normal">
              <thead>
                <tr className="border-b border-[#E5E7EB] bg-[#F3F3F4]/60 text-sm text-[#52555A]">
                  <th className="px-6 py-3 font-normal">Nazwa pliku</th>
                  <th className="px-6 py-3 font-normal">Format</th>
                  <th className="px-6 py-3 font-normal">Status przetwarzania</th>
                  <th className="px-6 py-3 text-right font-normal">Akcja</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#E5E7EB]">
                {filteredJobs.map((job) => (
                  <tr key={job.job_id} className="hover:bg-[#F3F3F4]/40">
                    <td className="px-6 py-3.5 text-[#0D0E0E]">
                      <div>{job.filename}</div>
                      {job.status === 'FAILED' && job.error && (
                        <div className="mt-1 text-xs text-[#CA0049]">{job.error.message}</div>
                      )}
                    </td>
                    <td className="px-6 py-3.5 text-[#52555A]">{getFileFormat(job.filename)}</td>
                    <td className={`px-6 py-3.5 ${getStatusClass(job)}`}>{getStatusLabel(job)}</td>
                    <td className="px-6 py-3.5 text-right">
                      {job.status === 'SUCCEEDED' ? (
                        <div className="inline-flex items-center gap-4">
                          <button
                            type="button"
                            onClick={() => onDownloadJobJson(job.job_id)}
                            className="text-sm font-normal text-[#52555A] hover:text-[#0D0E0E] hover:underline"
                          >
                            JSON
                          </button>
                          <button
                            type="button"
                            onClick={() => onSelectJob(job.job_id)}
                            className="text-sm font-normal text-[#08255E] hover:underline"
                          >
                            Otwórz
                          </button>
                        </div>
                      ) : (
                        <span className="text-sm text-[#9FA1A5]">Oczekiwanie</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

export function mergeJobSummaries(existing: JobView[], summaries: JobSummary[]): JobView[] {
  const byId = new Map<string, JobView>();
  for (const job of existing) {
    byId.set(job.job_id, job);
  }
  for (const summary of summaries) {
    const prev = byId.get(summary.job_id);
    byId.set(summary.job_id, {
      ...prev,
      ...summary,
      result: prev?.result,
      progress: summary.status === 'IN_PROGRESS' ? prev?.progress : undefined,
    });
  }
  return Array.from(byId.values()).sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );
}
