/**
 * Funkcje integracji z endpointami REST API analizy, kolejki, batch i zatwierdzania.
 */

import type { FormDefinition } from '../types/coiForm';
import type {
  AnalysisResult,
  ApprovalView,
  BatchAccepted,
  BatchView,
  HealthResponse,
  JobAccepted,
  JobList,
  JobStatus,
  JobView,
} from '../types/auditReport';
import { sanitizeFormForExport } from '../lib/zodSchemaBuilder';
import { apiJson, apiRequestRaw } from './client';

export type AnalyzeResponse =
  | { kind: 'accepted'; data: JobAccepted }
  | { kind: 'completed'; data: AnalysisResult };

/**
 * Wysyła pojedynczy plik do `POST /api/v1/forms/analyze`.
 * Domyślnie używa `?wait=0`, aby natychmiast otrzymać `202` z `job_id`
 * (umożliwia podgląd 7-etapowego postępu oraz późniejsze `PUT /jobs/{id}/approval`).
 */
export async function analyzeForm(
  file: File,
  options: { wait?: number; signal?: AbortSignal } = {},
): Promise<AnalyzeResponse> {
  const wait = options.wait ?? 0;
  const formData = new FormData();
  formData.append('file', file, file.name);

  const response = await apiRequestRaw(`/api/v1/forms/analyze?wait=${encodeURIComponent(wait)}`, {
    method: 'POST',
    body: formData,
    signal: options.signal,
  });

  if (response.status === 202) {
    const data = (await response.json()) as JobAccepted;
    return { kind: 'accepted', data };
  }

  const data = (await response.json()) as AnalysisResult;
  return { kind: 'completed', data };
}

/**
 * Wysyła wiele plików do `POST /api/v1/forms/batch`.
 */
export async function analyzeBatch(
  files: File[],
  options: { signal?: AbortSignal } = {},
): Promise<BatchAccepted> {
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file, file.name);
  }
  return apiJson<BatchAccepted>('/api/v1/forms/batch', {
    method: 'POST',
    body: formData,
    signal: options.signal,
  });
}

export async function getJob(jobId: string, signal?: AbortSignal): Promise<JobView> {
  return apiJson<JobView>(`/api/v1/jobs/${encodeURIComponent(jobId)}`, { signal });
}

export async function listJobs(
  params: {
    status?: JobStatus;
    batchId?: string;
    limit?: number;
    offset?: number;
  } = {},
  signal?: AbortSignal,
): Promise<JobList> {
  const search = new URLSearchParams();
  if (params.status) search.set('status', params.status);
  if (params.batchId) search.set('batch_id', params.batchId);
  if (params.limit !== undefined) search.set('limit', String(params.limit));
  if (params.offset !== undefined) search.set('offset', String(params.offset));
  const query = search.toString();
  return apiJson<JobList>(`/api/v1/jobs${query ? `?${query}` : ''}`, { signal });
}

export async function getBatch(batchId: string, signal?: AbortSignal): Promise<BatchView> {
  return apiJson<BatchView>(`/api/v1/batches/${encodeURIComponent(batchId)}`, { signal });
}

export async function approveForm(
  jobId: string,
  form: FormDefinition,
  signal?: AbortSignal,
): Promise<ApprovalView> {
  const sanitized = sanitizeFormForExport(form);
  return apiJson<ApprovalView>(`/api/v1/jobs/${encodeURIComponent(jobId)}/approval`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sanitized),
    signal,
  });
}

export async function getApproval(jobId: string, signal?: AbortSignal): Promise<ApprovalView> {
  return apiJson<ApprovalView>(`/api/v1/jobs/${encodeURIComponent(jobId)}/approval`, { signal });
}

export async function checkHealth(signal?: AbortSignal): Promise<HealthResponse> {
  try {
    const res = await apiRequestRaw('/health', { signal });
    const ct = res.headers.get('content-type') ?? '';
    if (ct.includes('application/json')) {
      return (await res.json()) as HealthResponse;
    }
  } catch {
    // Za nginxem w Dockerze proxy obejmuje wyłącznie /api/ — weryfikujemy przez /api/openapi.json.
  }

  const openapi = await apiJson<{ info?: { version?: string } }>('/api/openapi.json', { signal });
  return {
    status: 'ok',
    version: openapi.info?.version ?? 'unknown',
  };
}

export async function checkReady(signal?: AbortSignal): Promise<HealthResponse> {
  return apiJson<HealthResponse>('/ready', { signal });
}

/**
 * Pobiera czysty plik importu COI JSON (po sanityzacji) jako plik `.json`.
 */
export function downloadFormJson(form: FormDefinition, baseFilename?: string): void {
  const sanitized = sanitizeFormForExport(form);
  const content = JSON.stringify(sanitized, null, 2);
  const blob = new Blob([content], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);

  const safeName = (baseFilename || form.title || 'formularz_coi')
    .replace(/\.[^.]+$/, '')
    .replace(/[^a-zA-Z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ_-]+/g, '_')
    .slice(0, 80);

  const link = document.createElement('a');
  link.href = url;
  link.download = `${safeName || 'formularz_coi'}.json`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
