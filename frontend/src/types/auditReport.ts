/**
 * Raport jakości oraz kontrakty REST API (1:1 z backend/shared/schemas/quality.py,
 * backend/shared/jobs.py oraz backend/api/schemas.py).
 *
 * Klucze są w snake_case zgodnie z serializacją backendu.
 */

import type { FormDefinition } from './coiForm';

export type Severity = 'ERROR' | 'WARNING' | 'INFO';

export type ConfidenceLevel = 'HIGH' | 'MEDIUM' | 'LOW';

export type SourceChannel = 'TEXT' | 'ACROFORM' | 'OCR' | 'VISUAL' | 'RULE';

export type StageStatus = 'OK' | 'SKIPPED' | 'DEGRADED';

export interface Issue {
  code: string;
  severity: Severity;
  message: string;
  path?: string;
  field_id?: string | null;
}

export interface BoundingBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface FieldSource {
  channel: SourceChannel;
  page?: number | null;
  bbox?: BoundingBox | null;
  text?: string | null;
  element_id?: string | null;
}

export interface FieldAssessment {
  field_id: string;
  tech_name: string;
  confidence: number;
  level: ConfidenceLevel;
  sources: FieldSource[];
  signals: Record<string, number>;
  issues: Issue[];
}

export interface SkippedElement {
  reason: string;
  source: FieldSource;
}

export interface StageReport {
  name: string;
  status: StageStatus;
  duration_seconds: number;
  detail?: string | null;
}

export interface DictionaryEntry {
  code: string;
  label: string;
}

export interface DictionaryProposal {
  tech_name: string;
  name: string;
  entries: DictionaryEntry[];
  usage?: string | null;
}

export interface QualityReport {
  document_sha256: string;
  document_type?: string | null;
  accuracy_mode: string;
  created_at: string;
  fields: FieldAssessment[];
  issues: Issue[];
  skipped_elements: SkippedElement[];
  repair_iterations: number;
  models: Record<string, string>;
  stages: StageReport[];
  dictionary_proposals: DictionaryProposal[];
  demo_mode: boolean;
  usage: Record<string, number>;
}

export interface AnalysisResult {
  form: FormDefinition;
  report: QualityReport;
}

// ---------------------------------------------------------------- REST API: zadania i paczki

export type JobStatus = 'QUEUED' | 'IN_PROGRESS' | 'SUCCEEDED' | 'FAILED';

export interface JobProgress {
  stage: string;
  step: number;
  total_steps: number;
  message?: string | null;
}

export interface JobError {
  code: string;
  message: string;
}

export interface JobAccepted {
  job_id: string;
  status: JobStatus;
  filename: string;
  status_url: string;
}

export interface RejectedFile {
  filename: string | null;
  code: string;
  message: string;
}

export interface BatchAccepted {
  batch_id: string;
  status_url: string;
  jobs: JobAccepted[];
  rejected: RejectedFile[];
}

export interface JobSummary {
  job_id: string;
  status: JobStatus;
  filename: string;
  batch_id?: string | null;
  attempts: number;
  created_at: string;
  finished_at?: string | null;
  error?: JobError | null;
  approved_at?: string | null;
}

export interface JobView extends JobSummary {
  started_at?: string | null;
  progress?: JobProgress | null;
  retry_reason?: string | null;
  result?: AnalysisResult | null;
}

export interface JobList {
  items: JobSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface BatchView {
  batch_id: string;
  created_at: string;
  total_files: number;
  finished: boolean;
  counts: Record<JobStatus, number>;
  jobs: JobSummary[];
  rejected: RejectedFile[];
}

export interface ApprovalView {
  job_id: string;
  approved_by: string;
  approved_at: string;
  form: FormDefinition;
  warnings: Issue[];
}

export interface HealthResponse {
  status: string;
  version: string;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: unknown;
}

export interface ApiErrorResponse {
  error: ApiErrorBody;
}

export const SIGNAL_LABELS: Record<string, string> = {
  source_found: 'Tekst źródłowy w dokumencie',
  source_element: 'Element dokumentu (strona/bbox)',
  ocr: 'Pewność OCR',
  rules: 'Zgodność z regułami COI',
  domain_rules: 'Zgodność z regułami dziedzinowymi',
  visual_match: 'Potwierdzenie wizualne (OpenCV/VLM)',
  dual_read: 'Zgodność podwójnego odczytu',
};

export const STAGE_LABELS: Record<string, string> = {
  extraction: 'Odczyt treści i obrazu dokumentu',
  text_extraction: 'Ekstrakcja tekstu i tabel',
  visual_analysis: 'Analiza wizualna (OpenCV + VLM)',
  document_type: 'Rozpoznanie typu wniosku',
  structure: 'Struktura stron i sekcji',
  fields: 'Ekstrakcja pól formularza',
  dependencies: 'Zależności i przepływ stron',
  rules: 'Reguły dziedzinowe i słowniki',
  control: 'Kontrola jakości i raport',
};

export const CHANNEL_LABELS: Record<SourceChannel, string> = {
  TEXT: 'Warstwa tekstowa',
  ACROFORM: 'Pole AcroForm (PDF)',
  OCR: 'OCR (Tesseract)',
  VISUAL: 'Analiza wizualna',
  RULE: 'Reguła / szablon COI',
};
