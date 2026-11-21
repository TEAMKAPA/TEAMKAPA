import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Download,
  FileWarning,
  Loader2,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react';
import {
  STAGE_LABELS,
  type BatchView,
  type JobStatus,
  type JobSummary,
  type JobView,
} from '../../types/auditReport';
import { Badge, type BadgeVariant } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card, CardHeader } from '../ui/Card';

interface BatchQueueProps {
  jobs: JobView[];
  activeBatch: BatchView | null;
  selectedJobId: string | null;
  statusFilter: JobStatus | 'ALL';
  onStatusFilterChange: (status: JobStatus | 'ALL') => void;
  onSelectJob: (jobId: string) => void;
  onDownloadJobJson: (jobId: string) => void;
  onRefresh: () => void;
  isRefreshing: boolean;
}

const STATUS_META: Record<
  JobStatus,
  { label: string; variant: BadgeVariant; icon: typeof Clock }
> = {
  QUEUED: { label: 'W kolejce', variant: 'neutral', icon: Clock },
  IN_PROGRESS: { label: 'Analizowanie', variant: 'info', icon: Loader2 },
  SUCCEEDED: { label: 'Gotowe', variant: 'success', icon: CheckCircle2 },
  FAILED: { label: 'Błąd', variant: 'danger', icon: AlertCircle },
};

function formatDateTime(iso?: string | null): string {
  if (!iso) return '—';
  try {
    return new Intl.DateTimeFormat('pl-PL', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function JobStatusBadge({ status }: { status: JobStatus }) {
  const meta = STATUS_META[status];
  const Icon = meta.icon;
  return (
    <Badge variant={meta.variant}>
      <Icon className={`h-3.5 w-3.5 ${status === 'IN_PROGRESS' ? 'animate-spin' : ''}`} />
      {meta.label}
    </Badge>
  );
}

export function BatchQueue({
  jobs,
  activeBatch,
  selectedJobId,
  statusFilter,
  onStatusFilterChange,
  onSelectJob,
  onDownloadJobJson,
  onRefresh,
  isRefreshing,
}: BatchQueueProps) {
  const filteredJobs =
    statusFilter === 'ALL' ? jobs : jobs.filter((job) => job.status === statusFilter);

  const counts = {
    ALL: jobs.length,
    QUEUED: jobs.filter((j) => j.status === 'QUEUED').length,
    IN_PROGRESS: jobs.filter((j) => j.status === 'IN_PROGRESS').length,
    SUCCEEDED: jobs.filter((j) => j.status === 'SUCCEEDED').length,
    FAILED: jobs.filter((j) => j.status === 'FAILED').length,
  };

  return (
    <div className="space-y-5">
      {activeBatch && (
        <Card className="border-blue-200 bg-blue-50/20">
          <CardHeader
            title={
              <div className="flex flex-wrap items-center gap-2.5">
                <span>Paczka wsadowa</span>
                <Badge variant={activeBatch.finished ? 'success' : 'info'}>
                  {activeBatch.finished ? 'Ukończono' : 'Przetwarzanie…'}
                </Badge>
              </div>
            }
            subtitle={`Łącznie plików: ${activeBatch.total_files} · Utworzono: ${formatDateTime(activeBatch.created_at)}`}
          />

          <div className="space-y-4 p-5">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
                <div className="text-xs font-medium text-slate-500">W kolejce</div>
                <div className="mt-1 text-2xl font-bold text-slate-800 tabular-nums">
                  {activeBatch.counts.QUEUED ?? 0}
                </div>
              </div>
              <div className="rounded-xl border border-blue-200 bg-white px-4 py-3">
                <div className="text-xs font-medium text-blue-700">W trakcie</div>
                <div className="mt-1 text-2xl font-bold text-blue-700 tabular-nums">
                  {activeBatch.counts.IN_PROGRESS ?? 0}
                </div>
              </div>
              <div className="rounded-xl border border-emerald-200 bg-white px-4 py-3">
                <div className="text-xs font-medium text-emerald-700">Gotowe</div>
                <div className="mt-1 text-2xl font-bold text-emerald-700 tabular-nums">
                  {activeBatch.counts.SUCCEEDED ?? 0}
                </div>
              </div>
              <div className="rounded-xl border border-rose-200 bg-white px-4 py-3">
                <div className="text-xs font-medium text-rose-700">Błędy / Odrzucone</div>
                <div className="mt-1 text-2xl font-bold text-rose-700 tabular-nums">
                  {(activeBatch.counts.FAILED ?? 0) + activeBatch.rejected.length}
                </div>
              </div>
            </div>

            {activeBatch.rejected.length > 0 && (
              <div className="rounded-xl border border-amber-200 bg-amber-50/80 p-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-amber-950">
                  <FileWarning className="h-4 w-4 text-amber-600" />
                  Pliki odrzucone podczas walidacji ({activeBatch.rejected.length})
                </div>
                <ul className="mt-2.5 space-y-2">
                  {activeBatch.rejected.map((rej, idx) => (
                    <li
                      key={`${rej.filename ?? 'brak'}-${idx}`}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-white px-3 py-2 text-sm"
                    >
                      <span className="font-medium text-slate-900">
                        {rej.filename ?? '(brak nazwy)'}
                      </span>
                      <span className="text-xs text-rose-700">{rej.message}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </Card>
      )}

      <Card>
        <CardHeader
          title="Lista zadań analizy"
          subtitle="Wybierz gotowy dokument, aby zweryfikować niepewne pola i zatwierdzić JSON COI"
          action={
            <Button variant="secondary" size="sm" onClick={onRefresh} disabled={isRefreshing}>
              <RefreshCw className={`h-4 w-4 ${isRefreshing ? 'animate-spin' : ''}`} />
              Odśwież
            </Button>
          }
        />

        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/70 px-5 py-3">
          {(
            [
              ['ALL', 'Wszystkie'],
              ['IN_PROGRESS', 'W trakcie'],
              ['QUEUED', 'W kolejce'],
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
                className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors tabular-nums ${
                  active
                    ? 'bg-slate-900 text-white shadow-2xs'
                    : 'bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-100 hover:text-slate-900'
                }`}
              >
                {label} ({counts[key]})
              </button>
            );
          })}
        </div>

        {filteredJobs.length === 0 ? (
          <div className="px-6 py-12 text-center">
            <p className="text-sm font-medium text-slate-700">Brak zadań na liście</p>
            <p className="mt-1 text-sm text-slate-500">
              Prześlij formularz powyżej albo kliknij „Załaduj przykład demo”, aby zobaczyć przykładową analizę.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {filteredJobs.map((job) => {
              const isSelected = job.job_id === selectedJobId;
              const progressPct = job.progress
                ? Math.min(100, Math.round((job.progress.step / job.progress.total_steps) * 100))
                : null;
              const needsReviewCount = job.result
                ? job.result.report.fields.filter((f) => f.level !== 'HIGH').length
                : null;

              return (
                <li
                  key={job.job_id}
                  className={`px-5 py-4 transition-colors ${
                    isSelected ? 'bg-blue-50/50' : 'hover:bg-slate-50/80'
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-4">
                    <div className="min-w-0 flex-1 space-y-1.5">
                      <div className="flex flex-wrap items-center gap-2.5">
                        <span className="truncate text-base font-semibold text-slate-900">
                          {job.filename}
                        </span>
                        <JobStatusBadge status={job.status} />
                        {job.approved_at && (
                          <Badge variant="success">
                            <ShieldCheck className="h-3.5 w-3.5" />
                            Zatwierdzono
                          </Badge>
                        )}
                        {needsReviewCount !== null && needsReviewCount > 0 && !job.approved_at && (
                          <Badge variant="warning">Do przeglądu: {needsReviewCount}</Badge>
                        )}
                      </div>

                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
                        <span>Dodano: {formatDateTime(job.created_at)}</span>
                        {job.finished_at && <span>Zakończono: {formatDateTime(job.finished_at)}</span>}
                        {job.attempts > 1 && <span>Próby: {job.attempts}</span>}
                      </div>

                      {job.status === 'IN_PROGRESS' && job.progress && (
                        <div className="mt-2 max-w-lg space-y-1.5">
                          <div className="flex items-center justify-between text-xs font-medium text-blue-900">
                            <span>
                              Krok {job.progress.step} z {job.progress.total_steps}:{' '}
                              {job.progress.message ||
                                STAGE_LABELS[job.progress.stage] ||
                                job.progress.stage}
                            </span>
                            <span className="tabular-nums">{progressPct}%</span>
                          </div>
                          <div className="h-2 w-full overflow-hidden rounded-full bg-blue-100">
                            <div
                              className="h-full rounded-full bg-blue-600 transition-all duration-300"
                              style={{ width: `${progressPct}%` }}
                            />
                          </div>
                        </div>
                      )}

                      {job.status === 'QUEUED' && job.retry_reason && (
                        <div className="mt-1.5 inline-flex items-center gap-1.5 rounded-lg bg-amber-50 px-3 py-1.5 text-xs text-amber-900">
                          <RotateCcw className="h-3.5 w-3.5 shrink-0" />
                          Ponowienie po błędzie przejściowym: {job.retry_reason}
                        </div>
                      )}

                      {job.status === 'FAILED' && job.error && (
                        <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900">
                          <span className="mr-2 font-mono text-xs font-semibold">
                            {job.error.code}
                          </span>
                          {job.error.message}
                        </div>
                      )}
                    </div>

                    <div className="flex shrink-0 items-center gap-2">
                      {job.status === 'SUCCEEDED' && (
                        <>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => onDownloadJobJson(job.job_id)}
                            title="Pobierz wygenerowany plik importu COI JSON"
                          >
                            <Download className="h-4 w-4" />
                            JSON
                          </Button>
                          <Button
                            variant="primary"
                            size="sm"
                            onClick={() => onSelectJob(job.job_id)}
                          >
                            Przeglądaj wynik
                            <ArrowRight className="h-4 w-4" />
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}

/** Konwertuje `JobSummary` z listy `/jobs` do `JobView`, zachowując istniejący wynik z pamięci. */
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
