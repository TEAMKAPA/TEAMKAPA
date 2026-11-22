import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  getFieldTitle,
  getSectionGrid,
  normalizeFormDefinition,
  type FormDefinition,
  type LeafField,
} from './types/coiForm';
import type {
  AnalysisResult,
  ApprovalView,
  BatchView,
  DictionaryProposal,
  FieldSource,
  JobStatus,
  JobView,
} from './types/auditReport';
import { ApiClientError, getStoredToken, setStoredToken } from './api/client';
import {
  analyzeBatch,
  analyzeForm,
  approveForm,
  checkHealth,
  downloadFormJson,
  getApproval,
  getBatch,
  getJob,
  listJobs,
} from './api/forms';
import { sanitizeFormForExport, validateFormDefinitionRules } from './lib/zodSchemaBuilder';
import {
  MOCK_ANALYSIS_RESULT,
  MOCK_BATCH_VIEW,
  MOCK_DEMO_JOB_ID,
  MOCK_JOB_VIEW,
} from './mocks/sampleResponse';
import { Navbar, type AppSectionView } from './components/layout/Navbar';
import { FileDropzone } from './components/upload/FileDropzone';
import { BatchQueue, mergeJobSummaries } from './components/upload/BatchQueue';
import { SourcePreview } from './components/viewer/SourcePreview';
import { JsonViewer } from './components/viewer/JsonViewer';
import { FormRenderer } from './components/renderer/FormRenderer';
import { QualityReportPanel } from './components/audit/QualityReportPanel';
import { Button } from './components/ui/Button';
import { Textarea } from './components/ui/Input';
import { Modal } from './components/ui/Modal';

type ReviewTab = 'fields' | 'interactive' | 'report' | 'json';

function randomHexId(): string {
  const arr = new Uint8Array(16);
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(arr);
  } else {
    for (let i = 0; i < arr.length; i += 1) arr[i] = Math.floor(Math.random() * 256);
  }
  return Array.from(arr, (b) => b.toString(16).padStart(2, '0')).join('');
}

function cloneForm(form: FormDefinition): FormDefinition {
  return normalizeFormDefinition(form);
}

function cloneField(field: LeafField): LeafField {
  return JSON.parse(JSON.stringify(field)) as LeafField;
}

function findFieldInForm(
  form: FormDefinition,
  fieldId: string,
): { tabId: string; field: LeafField } | null {
  for (const tab of form.tabs ?? []) {
    for (const sq of tab.questions ?? []) {
      for (const fq of getSectionGrid(sq.field)) {
        if (fq.field.id === fieldId) {
          return { tabId: tab.id, field: fq.field };
        }
      }
    }
  }
  return null;
}

function replaceFieldInForm(form: FormDefinition, updated: LeafField): FormDefinition {
  return {
    ...form,
    tabs: (form.tabs ?? []).map((tab) => ({
      ...tab,
      questions: (tab.questions ?? []).map((sq) => ({
        ...sq,
        field: {
          ...sq.field,
          repetingSectionGrid: getSectionGrid(sq.field).map((fq) =>
            fq.field.id === updated.id ? { ...fq, field: updated } : fq,
          ),
        },
      })),
    })),
  };
}

export default function App() {
  const [activeView, setActiveView] = useState<AppSectionView>('upload');
  const [reviewTab, setReviewTab] = useState<ReviewTab>('fields');

  const [demoMode, setDemoMode] = useState(false);
  const [apiStatus, setApiStatus] = useState<'checking' | 'online' | 'offline'>('checking');

  const [jwtToken, setJwtToken] = useState<string>(() => getStoredToken());
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);
  const [tokenDraft, setTokenDraft] = useState<string>(() => getStoredToken());

  const [jobs, setJobs] = useState<JobView[]>([]);
  const [activeBatch, setActiveBatch] = useState<BatchView | null>(null);
  const [statusFilter, setStatusFilter] = useState<JobStatus | 'ALL'>('ALL');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  const localFilesRef = useRef<Map<string, File>>(new Map());
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeFilename, setActiveFilename] = useState<string>('');
  const [activeLocalFile, setActiveLocalFile] = useState<File | null>(null);
  const [isDemoDocument, setIsDemoDocument] = useState(false);
  const [originalResult, setOriginalResult] = useState<AnalysisResult | null>(null);
  const [editedForm, setEditedForm] = useState<FormDefinition | null>(null);
  const [activeTabId, setActiveTabId] = useState<string>('');
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [focusedSource, setFocusedSource] = useState<{ source: FieldSource; label?: string } | null>(
    null,
  );
  const [reviewedIds, setReviewedIds] = useState<Set<string>>(() => new Set());
  const [modifiedIds, setModifiedIds] = useState<Set<string>>(() => new Set());
  const [appliedDictTechNames, setAppliedDictTechNames] = useState<Set<string>>(() => new Set());
  const [approval, setApproval] = useState<ApprovalView | null>(null);
  const [isApproving, setIsApproving] = useState(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);

  useEffect(() => {
    if (!notice) return;
    const id = window.setTimeout(() => setNotice(null), 4500);
    return () => window.clearTimeout(id);
  }, [notice]);

  useEffect(() => {
    let cancelled = false;
    checkHealth()
      .then(() => {
        if (!cancelled) setApiStatus('online');
      })
      .catch(() => {
        if (!cancelled) setApiStatus('offline');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const refreshJobsList = useCallback(async () => {
    if (demoMode) return;
    setIsRefreshing(true);
    try {
      const response = await listJobs({ limit: 50 });
      setJobs((prev) => mergeJobSummaries(prev, response.items));
      setApiStatus('online');
      setAuthRequired(false);
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 401) {
        setAuthRequired(true);
      }
    } finally {
      setIsRefreshing(false);
    }
  }, [demoMode]);

  useEffect(() => {
    if (apiStatus === 'online' && !demoMode) {
      void refreshJobsList();
    }
  }, [apiStatus, demoMode, jwtToken, refreshJobsList]);

  const openAnalysisResult = useCallback(
    (
      result: AnalysisResult,
      filename: string,
      jobId: string | null,
      options: { demo?: boolean; existingApproval?: ApprovalView | null } = {},
    ) => {
      const baseForm = options.existingApproval?.form ?? result.form;
      setOriginalResult(result);
      setEditedForm(cloneForm(baseForm));
      setActiveJobId(jobId);
      setActiveFilename(filename);
      setActiveLocalFile(
        (jobId ? localFilesRef.current.get(jobId) : undefined) ??
          localFilesRef.current.get(filename) ??
          null,
      );
      setIsDemoDocument(Boolean(options.demo));
      setActiveTabId(baseForm.tabs[0]?.id ?? '');
      setSelectedFieldId(null);
      setFocusedSource(null);
      setCurrentPage(1);
      setReviewedIds(new Set());
      setModifiedIds(new Set());
      setAppliedDictTechNames(new Set());
      setApproval(options.existingApproval ?? null);
      setApprovalError(null);
      setReviewTab('fields');
      setActiveView('review');
    },
    [],
  );

  const handleLoadDemo = useCallback(() => {
    setDemoMode(true);
    setUploadError(null);
    setActiveBatch(MOCK_BATCH_VIEW);
    setJobs((prev) => {
      if (prev.some((j) => j.job_id === MOCK_DEMO_JOB_ID)) return prev;
      return [
        MOCK_JOB_VIEW,
        { ...MOCK_BATCH_VIEW.jobs[1], result: MOCK_ANALYSIS_RESULT },
        ...prev,
      ];
    });
    openAnalysisResult(MOCK_ANALYSIS_RESULT, MOCK_JOB_VIEW.filename, MOCK_DEMO_JOB_ID, {
      demo: true,
    });
    setNotice({ kind: 'success', text: 'Załadowano przykładowy dokument.' });
  }, [openAnalysisResult]);

  const handleSelectNavView = useCallback(
    (view: AppSectionView) => {
      if (view === 'review') {
        if (editedForm && originalResult) {
          setActiveView('review');
        } else {
          handleLoadDemo();
        }
        return;
      }

      setActiveView('upload');
    },
    [editedForm, originalResult, handleLoadDemo],
  );

  const simulateDemoJob = useCallback((file: File, batchId: string | null = null): JobView => {
    const jobId = randomHexId();
    localFilesRef.current.set(jobId, file);
    localFilesRef.current.set(file.name, file);
    const createdAt = new Date().toISOString();
    const initialJob: JobView = {
      job_id: jobId,
      status: 'IN_PROGRESS',
      filename: file.name,
      batch_id: batchId,
      attempts: 1,
      created_at: createdAt,
      started_at: createdAt,
      progress: {
        stage: 'extraction',
        step: 2,
        total_steps: 7,
        message: 'Odczyt treści dokumentu',
      },
    };

    window.setTimeout(() => {
      const finishedAt = new Date().toISOString();
      setJobs((prev) =>
        prev.map((j) =>
          j.job_id === jobId
            ? {
                ...j,
                status: 'SUCCEEDED',
                finished_at: finishedAt,
                progress: undefined,
                result: MOCK_ANALYSIS_RESULT,
              }
            : j,
        ),
      );
      if (batchId) {
        setActiveBatch((prev) => {
          if (!prev || prev.batch_id !== batchId) return prev;
          const nextJobs = prev.jobs.map((bj) =>
            bj.job_id === jobId
              ? { ...bj, status: 'SUCCEEDED' as const, finished_at: finishedAt }
              : bj,
          );
          const succeeded = nextJobs.filter((bj) => bj.status === 'SUCCEEDED').length;
          const inProg = nextJobs.filter((bj) => bj.status === 'IN_PROGRESS').length;
          return {
            ...prev,
            finished: inProg === 0,
            counts: { QUEUED: 0, IN_PROGRESS: inProg, SUCCEEDED: succeeded, FAILED: 0 },
            jobs: nextJobs,
          };
        });
      }
    }, 1200);

    return initialJob;
  }, []);

  const handleSubmitSingle = async (file: File) => {
    setUploadError(null);
    setIsSubmitting(true);
    try {
      if (demoMode) {
        const simulated = simulateDemoJob(file);
        setJobs((prev) => [simulated, ...prev]);
        setNotice({ kind: 'success', text: `Rozpoczęto przetwarzanie pliku „${file.name}”.` });
        return;
      }

      const response = await analyzeForm(file, { wait: 0 });
      if (response.kind === 'completed') {
        localFilesRef.current.set(file.name, file);
        openAnalysisResult(response.data, file.name, null, { demo: false });
        setNotice({ kind: 'success', text: `Zakończono analizę pliku „${file.name}”.` });
      } else {
        const accepted = response.data;
        localFilesRef.current.set(accepted.job_id, file);
        localFilesRef.current.set(accepted.filename, file);
        const newJob: JobView = {
          job_id: accepted.job_id,
          status: accepted.status,
          filename: accepted.filename,
          attempts: 0,
          created_at: new Date().toISOString(),
        };
        setJobs((prev) => [newJob, ...prev.filter((j) => j.job_id !== accepted.job_id)]);
        setNotice({ kind: 'success', text: `Dodano plik „${accepted.filename}” do listy.` });
      }
    } catch (err) {
      if (err instanceof ApiClientError) {
        if (err.status === 401) setAuthModalOpen(true);
        setUploadError(`${err.code}: ${err.message}`);
      } else {
        setUploadError('Nieoczekiwany błąd podczas wysyłania pliku.');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSubmitBatch = async (files: File[]) => {
    setUploadError(null);
    setIsSubmitting(true);
    try {
      if (demoMode) {
        const batchId = randomHexId();
        const createdJobs = files.map((f) => simulateDemoJob(f, batchId));
        setJobs((prev) => [...createdJobs, ...prev]);
        setActiveBatch({
          batch_id: batchId,
          created_at: new Date().toISOString(),
          total_files: files.length,
          finished: false,
          counts: { QUEUED: 0, IN_PROGRESS: createdJobs.length, SUCCEEDED: 0, FAILED: 0 },
          jobs: createdJobs,
          rejected: [],
        });
        setNotice({ kind: 'success', text: `Wysłano ${files.length} plików.` });
        return;
      }

      const accepted = await analyzeBatch(files);
      const fileByName = new Map(files.map((f) => [f.name, f]));
      for (const j of accepted.jobs) {
        const match = fileByName.get(j.filename);
        if (match) {
          localFilesRef.current.set(j.job_id, match);
          localFilesRef.current.set(j.filename, match);
        }
      }

      const now = new Date().toISOString();
      const newJobs: JobView[] = accepted.jobs.map((j) => ({
        job_id: j.job_id,
        status: j.status,
        filename: j.filename,
        batch_id: accepted.batch_id,
        attempts: 0,
        created_at: now,
      }));
      setJobs((prev) => {
        const ids = new Set(newJobs.map((j) => j.job_id));
        return [...newJobs, ...prev.filter((j) => !ids.has(j.job_id))];
      });
      setActiveBatch({
        batch_id: accepted.batch_id,
        created_at: now,
        total_files: accepted.jobs.length + accepted.rejected.length,
        finished: false,
        counts: { QUEUED: accepted.jobs.length, IN_PROGRESS: 0, SUCCEEDED: 0, FAILED: 0 },
        jobs: newJobs,
        rejected: accepted.rejected,
      });
      setNotice({
        kind: 'success',
        text: `Przyjęto plików: ${accepted.jobs.length}, odrzucono: ${accepted.rejected.length}.`,
      });
    } catch (err) {
      if (err instanceof ApiClientError) {
        if (err.status === 401) setAuthModalOpen(true);
        setUploadError(`${err.code}: ${err.message}`);
      } else {
        setUploadError('Nieoczekiwany błąd podczas wysyłania plików.');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const pendingJobIds = useMemo(
    () =>
      jobs
        .filter((j) => j.status === 'QUEUED' || j.status === 'IN_PROGRESS')
        .map((j) => j.job_id),
    [jobs],
  );
  const activeBatchPendingId = activeBatch && !activeBatch.finished ? activeBatch.batch_id : null;

  useEffect(() => {
    if (demoMode || (pendingJobIds.length === 0 && !activeBatchPendingId)) return;
    let cancelled = false;
    const poll = async () => {
      if (pendingJobIds.length > 0) {
        const updates = await Promise.allSettled(pendingJobIds.map((id) => getJob(id)));
        if (cancelled) return;
        const map = new Map<string, JobView>();
        for (const res of updates) {
          if (res.status === 'fulfilled') map.set(res.value.job_id, res.value);
        }
        if (map.size > 0) {
          setJobs((prev) => prev.map((j) => map.get(j.job_id) ?? j));
        }
      }
      if (activeBatchPendingId) {
        try {
          const updatedBatch = await getBatch(activeBatchPendingId);
          if (!cancelled) {
            setActiveBatch(updatedBatch);
            setJobs((prev) => mergeJobSummaries(prev, updatedBatch.jobs));
          }
        } catch {
          // Ponowienie w kolejnym cyklu
        }
      }
    };
    const intervalId = window.setInterval(() => {
      void poll();
    }, 2500);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [demoMode, pendingJobIds, activeBatchPendingId]);

  const handleSelectJob = async (jobId: string) => {
    const existing = jobs.find((j) => j.job_id === jobId);
    if (existing?.result) {
      const isDemoJob = demoMode || jobId === MOCK_DEMO_JOB_ID;
      openAnalysisResult(existing.result, existing.filename, jobId, { demo: isDemoJob });
      if (!isDemoJob && existing.approved_at) {
        try {
          const existingApproval = await getApproval(jobId);
          setApproval(existingApproval);
          setEditedForm(cloneForm(existingApproval.form));
        } catch {
          // Używamy wyniku pierwotnego
        }
      }
      return;
    }

    if (demoMode) {
      openAnalysisResult(MOCK_ANALYSIS_RESULT, existing?.filename ?? 'wniosek_demo.pdf', jobId, {
        demo: true,
      });
      return;
    }

    try {
      const fullJob = await getJob(jobId);
      setJobs((prev) => prev.map((j) => (j.job_id === jobId ? fullJob : j)));
      if (!fullJob.result) {
        setNotice({ kind: 'error', text: 'Dokument nie ma jeszcze gotowego wyniku.' });
        return;
      }
      let existingApproval: ApprovalView | null = null;
      if (fullJob.approved_at) {
        try {
          existingApproval = await getApproval(jobId);
        } catch {
          existingApproval = null;
        }
      }
      openAnalysisResult(fullJob.result, fullJob.filename, fullJob.job_id, {
        demo: false,
        existingApproval,
      });
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 401) {
        setAuthModalOpen(true);
      }
      setNotice({ kind: 'error', text: 'Nie udało się otworzyć dokumentu.' });
    }
  };

  const handleDownloadJobJson = async (jobId: string) => {
    const existing = jobs.find((j) => j.job_id === jobId);
    if (existing?.result) {
      downloadFormJson(existing.result.form, existing.filename);
      return;
    }
    if (demoMode) {
      downloadFormJson(MOCK_ANALYSIS_RESULT.form, existing?.filename ?? 'formularz_demo');
      return;
    }
    try {
      const fullJob = await getJob(jobId);
      if (fullJob.result) {
        setJobs((prev) => prev.map((j) => (j.job_id === jobId ? fullJob : j)));
        downloadFormJson(fullJob.result.form, fullJob.filename);
      }
    } catch {
      setNotice({ kind: 'error', text: 'Nie udało się pobrać pliku JSON.' });
    }
  };

  // --- Logika weryfikacji ---
  const liveRuleIssues = useMemo(
    () => (editedForm ? validateFormDefinitionRules(editedForm) : []),
    [editedForm],
  );

  const liveRuleErrorCount = useMemo(
    () => liveRuleIssues.filter((i) => i.severity === 'ERROR').length,
    [liveRuleIssues],
  );

  const fieldLabels = useMemo(() => {
    const map = new Map<string, string>();
    if (!editedForm) return map;
    for (const tab of editedForm.tabs ?? []) {
      for (const sq of tab.questions ?? []) {
        for (const fq of getSectionGrid(sq.field)) {
          map.set(fq.field.id, getFieldTitle(fq.field));
        }
      }
    }
    return map;
  }, [editedForm]);

  const handleSelectField = useCallback(
    (fieldId: string | null) => {
      setSelectedFieldId(fieldId);
      if (!fieldId || !editedForm || !originalResult) {
        setFocusedSource(null);
        return;
      }

      const found = findFieldInForm(editedForm, fieldId);
      if (found) {
        setActiveTabId(found.tabId);
      }

      const assessment = originalResult.report.fields.find((f) => f.field_id === fieldId);
      const firstSource = assessment?.sources[0];
      if (firstSource) {
        if (firstSource.page) setCurrentPage(firstSource.page);
        setFocusedSource({
          source: firstSource,
          label: found ? getFieldTitle(found.field) : assessment?.tech_name,
        });
      } else {
        setFocusedSource(null);
      }

      if (reviewTab === 'report' || reviewTab === 'json') {
        setReviewTab('fields');
      }
    },
    [editedForm, originalResult, reviewTab],
  );

  const handleFocusSource = useCallback((source: FieldSource, label?: string) => {
    if (source.page) setCurrentPage(source.page);
    setFocusedSource({ source, label });
    setSelectedFieldId(null);
  }, []);

  const handleFocusSelectedOnDocument = useCallback(() => {
    if (!selectedFieldId || !originalResult) return;
    const assessment = originalResult.report.fields.find((f) => f.field_id === selectedFieldId);
    const firstSource = assessment?.sources[0];
    if (firstSource) {
      if (firstSource.page) setCurrentPage(firstSource.page);
      setFocusedSource({
        source: firstSource,
        label: fieldLabels.get(selectedFieldId) ?? assessment?.tech_name,
      });
    }
  }, [selectedFieldId, originalResult, fieldLabels]);

  const handleUpdateField = useCallback((updated: LeafField) => {
    setEditedForm((prev) => (prev ? replaceFieldInForm(prev, updated) : prev));
    setModifiedIds((prev) => {
      const next = new Set(prev);
      next.add(updated.id);
      return next;
    });
    setApprovalError(null);
  }, []);

  const handleResetField = useCallback(
    (fieldId: string) => {
      if (!originalResult) return;
      const orig = findFieldInForm(originalResult.form, fieldId);
      if (!orig) return;
      setEditedForm((prev) => (prev ? replaceFieldInForm(prev, cloneField(orig.field)) : prev));
      setModifiedIds((prev) => {
        const next = new Set(prev);
        next.delete(fieldId);
        return next;
      });
    },
    [originalResult],
  );

  const handleToggleReviewed = useCallback((fieldId: string) => {
    setReviewedIds((prev) => {
      const next = new Set(prev);
      if (next.has(fieldId)) next.delete(fieldId);
      else next.add(fieldId);
      return next;
    });
  }, []);

  const handleApplyDictionaryProposal = useCallback(
    (proposal: DictionaryProposal) => {
      if (!editedForm) return;
      let targetField: LeafField | null = null;
      for (const tab of editedForm.tabs ?? []) {
        for (const sq of tab.questions ?? []) {
          for (const fq of getSectionGrid(sq.field)) {
            if (fq.field.techName === proposal.tech_name) {
              targetField = fq.field;
            }
          }
        }
      }
      if (!targetField) return;

      const isRequired =
        'validation' in targetField &&
        (targetField.validation as readonly string[]).includes('REQUIRED');

      const converted: LeafField = {
        id: targetField.id,
        techName: targetField.techName,
        questionOrder: targetField.questionOrder,
        tooltipMessage: targetField.tooltipMessage,
        type: 'SELECT',
        title: 'title' in targetField ? targetField.title : proposal.name,
        description: 'description' in targetField ? targetField.description : undefined,
        readOnly: 'readOnly' in targetField ? targetField.readOnly : undefined,
        options: [],
        dictionaryName: proposal.name,
        searchEnabled: true,
        validation: isRequired ? ['REQUIRED'] : [],
        states: 'states' in targetField ? targetField.states : undefined,
      };

      handleUpdateField(converted);
      setAppliedDictTechNames((prev) => {
        const next = new Set(prev);
        next.add(proposal.tech_name);
        return next;
      });
      setNotice({ kind: 'success', text: `Zastosowano słownik „${proposal.name}”.` });
    },
    [editedForm, handleUpdateField],
  );

  const handleApproveAndExport = async () => {
    if (!editedForm) return;
    if (liveRuleErrorCount > 0) {
      setApprovalError(`Popraw błędy reguł przed zatwierdzeniem (${liveRuleErrorCount}).`);
      return;
    }

    setApprovalError(null);
    setIsApproving(true);
    try {
      const sanitized = sanitizeFormForExport(editedForm);
      if (demoMode || !activeJobId || activeJobId === MOCK_DEMO_JOB_ID) {
        const now = new Date().toISOString();
        const mockApproval: ApprovalView = {
          job_id: activeJobId ?? MOCK_DEMO_JOB_ID,
          approved_at: now,
          approved_by: 'urzednik',
          form: sanitized,
          warnings: liveRuleIssues.filter((i) => i.severity === 'WARNING'),
        };
        setApproval(mockApproval);
        setJobs((prev) =>
          prev.map((j) =>
            j.job_id === mockApproval.job_id ? { ...j, approved_at: now } : j,
          ),
        );
        downloadFormJson(editedForm, activeFilename);
        setNotice({ kind: 'success', text: 'Formularz został zatwierdzony i wyeksportowany.' });
        return;
      }

      const saved = await approveForm(activeJobId, editedForm);
      setApproval(saved);
      setJobs((prev) =>
        prev.map((j) =>
          j.job_id === activeJobId ? { ...j, approved_at: saved.approved_at } : j,
        ),
      );
      downloadFormJson(editedForm, activeFilename);
      setNotice({ kind: 'success', text: 'Formularz został zatwierdzony i wyeksportowany.' });
    } catch (err) {
      if (err instanceof ApiClientError) {
        if (err.status === 401) setAuthModalOpen(true);
        setApprovalError(`${err.code}: ${err.message}`);
      } else {
        setApprovalError('Nie udało się zatwierdzić formularza.');
      }
    } finally {
      setIsApproving(false);
    }
  };

  const handleSaveToken = () => {
    setStoredToken(tokenDraft);
    const cleaned = tokenDraft.trim();
    setJwtToken(cleaned);
    if (cleaned) setAuthRequired(false);
    setAuthModalOpen(false);
    setNotice({
      kind: 'success',
      text: cleaned ? 'Zapisano token API.' : 'Usunięto token API.',
    });
  };

  return (
    <div className="min-h-screen bg-[#F3F3F4] text-[#0D0E0E]">
      <Navbar
        activeView={activeView}
        demoMode={demoMode}
        hasToken={Boolean(jwtToken)}
        hasActiveForm={Boolean(editedForm)}
        onSelectView={handleSelectNavView}
        onToggleDemo={() => {
          setDemoMode((prev) => !prev);
          setUploadError(null);
        }}
        onOpenAuth={() => {
          setTokenDraft(jwtToken);
          setAuthModalOpen(true);
        }}
      />

      <main
        className={`mx-auto space-y-6 px-4 py-6 sm:px-6 sm:py-8 ${
          activeView === 'review' ? 'max-w-[1760px]' : 'max-w-[1400px]'
        }`}
      >
        {notice && (
          <div
            role="status"
            className={`rounded-lg border px-4 py-3 text-sm font-normal ${
              notice.kind === 'success'
                ? 'border-[#A7F3D0] bg-[#ECFDF5] text-[#065F46]'
                : 'border-[#CA0049] bg-[#FFF1F2] text-[#9F1239]'
            }`}
          >
            {notice.text}
          </div>
        )}

        {/* WIDOK B: Ekran weryfikacji formularza (Split Screen — szeroka prawa sekcja) */}
        {activeView === 'review' && editedForm && originalResult ? (
          <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(380px,38%)_1fr]">
            {/* Lewy panel: Podgląd dokumentu */}
            <div className="lg:sticky lg:top-6">
              <SourcePreview
                filename={activeFilename}
                localFile={activeLocalFile}
                isDemoDocument={isDemoDocument}
                fields={originalResult.report.fields}
                skippedElements={originalResult.report.skipped_elements}
                selectedFieldId={selectedFieldId}
                focusedSource={focusedSource}
                currentPage={currentPage}
                onPageChange={setCurrentPage}
                onSelectField={(id) => handleSelectField(id)}
                onAttachLocalFile={(file) => {
                  if (activeJobId) localFilesRef.current.set(activeJobId, file);
                  localFilesRef.current.set(activeFilename, file);
                  setActiveLocalFile(file);
                  setIsDemoDocument(false);
                }}
                fieldLabels={fieldLabels}
              />
            </div>

            {/* Prawy panel: Wyekstrahowane dane + pasek akcji na dole */}
            <div className="flex flex-col overflow-hidden rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] shadow-none">
              {/* Zakładki trybu weryfikacji — czysty tekst z podkreśleniem */}
              <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#E5E7EB] px-4 sm:px-6">
                <div className="flex flex-wrap items-center gap-6" role="tablist">
                  {(
                    [
                      ['fields', 'Pola'],
                      ['interactive', 'Podgląd wypełniania'],
                      ['report', 'Raport jakości'],
                      ['json', 'Podgląd JSON'],
                    ] as const
                  ).map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      role="tab"
                      aria-selected={reviewTab === key}
                      onClick={() => setReviewTab(key)}
                      className={`-mb-px border-b-2 py-3 text-[15px] font-normal transition-colors ${
                        reviewTab === key
                          ? 'border-[#08255E] text-[#08255E]'
                          : 'border-transparent text-[#52555A] hover:text-[#0D0E0E]'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                {approval && (
                  <span className="text-sm font-normal text-[#065F46]">Zatwierdzono</span>
                )}
              </div>

              {/* Edycja głównego tytułu formularza */}
              <div className="flex flex-col gap-1.5 border-b border-[#E5E7EB] px-4 py-3 sm:flex-row sm:items-center sm:gap-3 sm:px-6">
                <label
                  htmlFor="main-form-title-input"
                  className="shrink-0 text-sm font-normal text-[#52555A]"
                >
                  Tytuł formularza:
                </label>
                <input
                  id="main-form-title-input"
                  type="text"
                  aria-label="Tytuł formularza"
                  value={editedForm.title}
                  onChange={(e) =>
                    setEditedForm((prev) => (prev ? { ...prev, title: e.target.value } : prev))
                  }
                  className="w-full min-w-0 flex-1 rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-3.5 py-2 text-[16px] font-normal text-[#0D0E0E] shadow-none focus:border-[#08255E] focus:outline-none"
                />
              </div>

              {/* Zawartość prawego panelu */}
              <div>
                {(reviewTab === 'fields' || reviewTab === 'interactive') && (
                  <FormRenderer
                    mode={reviewTab === 'fields' ? 'review' : 'interactive'}
                    form={editedForm}
                    assessments={originalResult.report.fields}
                    liveRuleIssues={liveRuleIssues}
                    selectedFieldId={selectedFieldId}
                    activeTabId={activeTabId}
                    onActiveTabChange={setActiveTabId}
                    reviewedIds={reviewedIds}
                    modifiedIds={modifiedIds}
                    onSelectField={handleSelectField}
                    onUpdateField={handleUpdateField}
                    onResetField={handleResetField}
                    onToggleReviewed={handleToggleReviewed}
                    onFocusSelectedOnDocument={handleFocusSelectedOnDocument}
                  />
                )}

                {reviewTab === 'report' && (
                  <QualityReportPanel
                    report={originalResult.report}
                    liveRuleIssues={liveRuleIssues}
                    appliedDictionaryTechNames={appliedDictTechNames}
                    onSelectField={(id) => handleSelectField(id)}
                    onFocusSource={handleFocusSource}
                    onApplyDictionaryProposal={handleApplyDictionaryProposal}
                  />
                )}

                {reviewTab === 'json' && (
                  <JsonViewer
                    form={editedForm}
                    report={originalResult.report}
                    liveRuleIssues={liveRuleIssues}
                    filename={activeFilename}
                  />
                )}
              </div>

              {/* Pasek akcji na dole prawego panelu */}
              <div className="border-t border-[#E5E7EB] bg-[#FFFFFF] px-6 py-4">
                {approvalError && (
                  <div
                    role="alert"
                    className="mb-3 rounded-lg border border-[#CA0049] bg-[#FFF1F2] px-4 py-2.5 text-sm font-normal text-[#9F1239]"
                  >
                    {approvalError}
                  </div>
                )}

                <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <button
                    type="button"
                    onClick={() => setActiveView('upload')}
                    className="w-full rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-5 py-2.5 text-[15px] font-normal text-[#0D0E0E] hover:bg-[#F3F3F4] sm:w-auto"
                  >
                    Wróć do listy
                  </button>

                  <button
                    type="button"
                    data-testid="approve-form-button"
                    disabled={isApproving}
                    onClick={handleApproveAndExport}
                    className="w-full rounded-lg bg-[#CA0049] px-6 py-3 text-[15px] font-normal text-[#FCE6EE] shadow-none transition-colors hover:bg-[#A8003D] disabled:opacity-50 sm:w-auto"
                  >
                    {isApproving ? 'Zapisywanie...' : 'Zatwierdź i eksportuj'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* WIDOK A: Wgrywanie plików i kolejka dokumentów */
          <div className="space-y-6">
            {apiStatus === 'online' && authRequired && !demoMode && (
              <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-6 py-4 text-sm font-normal">
                <span>API wymaga tokenu JWT. Wklej token lub uruchom tryb demo.</span>
                <div className="flex items-center gap-3">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      setTokenDraft(jwtToken);
                      setAuthModalOpen(true);
                    }}
                  >
                    Wpisz token API
                  </Button>
                  <Button variant="secondary" size="sm" onClick={handleLoadDemo}>
                    Tryb demo
                  </Button>
                </div>
              </div>
            )}

            <FileDropzone
              onSubmitSingle={handleSubmitSingle}
              onSubmitBatch={handleSubmitBatch}
              onLoadDemo={handleLoadDemo}
              isSubmitting={isSubmitting}
              apiError={uploadError}
              onDismissError={() => setUploadError(null)}
            />

            <BatchQueue
              jobs={jobs}
              activeBatch={activeBatch}
              statusFilter={statusFilter}
              onStatusFilterChange={setStatusFilter}
              onSelectJob={handleSelectJob}
              onDownloadJobJson={handleDownloadJobJson}
              onRefresh={refreshJobsList}
              isRefreshing={isRefreshing}
            />
          </div>
        )}
      </main>

      <Modal
        open={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        title="Token API (JWT)"
        subtitle="Token zapisywany jest tylko na czas bieżącej sesji przeglądarki."
      >
        <div className="space-y-4 text-sm font-normal">
          <div className="rounded-lg border border-[#E5E7EB] bg-[#F3F3F4] p-3.5 text-[#52555A]">
            <p className="text-[#0D0E0E]">Polecenie generowania tokenu w katalogu TEAMKAPA:</p>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-[#04122F] p-3 font-mono text-xs text-white">
              docker compose exec -T api python -m backend.api.token_cli --sub urzednik --minutes 480
            </pre>
          </div>

          <label className="block space-y-1.5">
            <span className="text-[#0D0E0E]">Token JWT</span>
            <Textarea
              rows={3}
              value={tokenDraft}
              onChange={(e) => setTokenDraft(e.target.value)}
              placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
              className="font-mono text-xs"
            />
          </label>

          <div className="flex items-center justify-between gap-3 pt-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setTokenDraft('');
                setStoredToken('');
                setJwtToken('');
              }}
            >
              Wyczyść
            </Button>
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" onClick={() => setAuthModalOpen(false)}>
                Anuluj
              </Button>
              <Button variant="navy" size="sm" onClick={handleSaveToken}>
                Zapisz token
              </Button>
            </div>
          </div>
        </div>
      </Modal>
    </div>
  );
}
