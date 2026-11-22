import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { validateFormDefinitionRules } from '../lib/zodSchemaBuilder';
import type { AnalysisResult } from '../types/auditReport';
import { normalizeFormDefinition } from '../types/coiForm';
import backendResult from './fixtures/backendAnalysisResult.json';

/**
 * Wynik w kształcie, w jakim zwraca go API (`GET /api/v1/jobs/{id}`) — fragment wyniku
 * pipeline'u: pola sekcji pod kluczem `repeatingSectionGrid`, `defaultValue` jako lista
 * (słownik Kraje: `["Polska"]`), edytowalne dane kontaktowe i adresowe z MLPZ, TEXT_BLOCK
 * na stronie końcowej, bez kluczy o wartości null (API je pomija).
 */
const RESULT = backendResult as unknown as AnalysisResult;
const JOB_ID = 'a'.repeat(32);
const SUMMARY = {
  job_id: JOB_ID,
  status: 'SUCCEEDED',
  filename: 'wniosek.docx',
  attempts: 1,
  created_at: '2026-11-22T10:00:00Z',
  finished_at: '2026-11-22T10:01:00Z',
};
const FIELD_COUNT = 19;

/** Formularz wysłany do API / pobrany jako plik — klucz sekcji jak w plikach COI. */
interface ExportedForm {
  tabs: { questions: { field: { repeatingSectionGrid: { field: Record<string, unknown> }[] } }[] }[];
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

/** API z jednym zakończonym zadaniem; zwraca treść ostatniego zatwierdzenia (PUT). */
function mockApi(): { approvedBody: () => unknown } {
  let approved: unknown = null;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith('/health')) return jsonResponse({ status: 'ok', version: 'test' });
    if (url.includes('/api/v1/jobs?')) {
      return jsonResponse({ items: [SUMMARY], total: 1, limit: 50, offset: 0 });
    }
    if (url.endsWith(`/api/v1/jobs/${JOB_ID}/approval`) && init?.method === 'PUT') {
      approved = JSON.parse(String(init.body));
      return jsonResponse({
        job_id: JOB_ID,
        approved_by: 'urzednik',
        approved_at: '2026-11-22T10:05:00Z',
        form: approved,
        warnings: [],
      });
    }
    if (url.endsWith(`/api/v1/jobs/${JOB_ID}`)) return jsonResponse({ ...SUMMARY, result: RESULT });
    return new Response(null, { status: 404 });
  });
  return { approvedBody: () => approved };
}

/** Przechwytuje plik JSON pobierany przez przeglądarkę (Blob przekazany do createObjectURL). */
function captureDownloads(): Blob[] {
  const blobs: Blob[] = [];
  vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
    blobs.push(blob as Blob);
    return 'blob:test';
  });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  return blobs;
}

/** Blob z jsdom nie ma `text()` — odczyt przez FileReader. */
function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

async function openJob(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  const openButtons = await screen.findAllByRole('button', { name: 'Otwórz' });
  await user.click(openButtons[0]);
  await screen.findByRole('heading', { name: 'Rozpoznane pola' });
}

describe('Wynik analizy prosto z backendu (fragment prawdziwego wyniku pipeline’u)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('otwiera wynik z API i pokazuje rozpoznane pola', async () => {
    mockApi();
    const user = userEvent.setup();
    render(<App />);

    await openJob(user);

    expect(screen.getByRole('option', { name: `Wszystkie (${FIELD_COUNT})` })).toBeInTheDocument();
    expect(screen.getByText('Urząd, do którego jest składany wniosek')).toBeInTheDocument();
    expect(screen.getByText('4. Strona końcowa')).toBeInTheDocument();
  });

  it('pobiera JSON wyniku z listy zadań w formacie plików COI', async () => {
    mockApi();
    const downloads = captureDownloads();
    const user = userEvent.setup();
    render(<App />);

    const jsonButtons = await screen.findAllByRole('button', { name: 'JSON' });
    await user.click(jsonButtons[0]);

    await waitFor(() => expect(downloads).toHaveLength(1));
    const text = await readBlob(downloads[0]);
    expect(text).not.toContain('repetingSectionGrid');
    const exported = JSON.parse(text) as ExportedForm;
    const country = exported.tabs[0].questions[2].field.repeatingSectionGrid[0].field;
    expect(country.techName).toBe('kraj');
    expect(country.defaultValue).toEqual(['Polska']);
  });

  it('reguły frontendu przyjmują wynik backendu (FINISH z TEXT_BLOCK, edytowalne dane z MLPZ)', () => {
    const issues = validateFormDefinitionRules(normalizeFormDefinition(RESULT.form));
    expect(issues.filter((issue) => issue.severity === 'ERROR')).toEqual([]);
  });

  it('zatwierdza wynik bez poprawek i wysyła komplet pól', async () => {
    const api = mockApi();
    captureDownloads();
    const user = userEvent.setup();
    render(<App />);

    await openJob(user);
    await user.click(screen.getByTestId('approve-form-button'));

    expect(await screen.findByText('Zatwierdzono')).toBeInTheDocument();
    const body = api.approvedBody() as ExportedForm;
    const leaves = body.tabs.flatMap((tab) =>
      tab.questions.flatMap((question) => question.field.repeatingSectionGrid),
    );
    expect(leaves).toHaveLength(FIELD_COUNT);
  });
});
