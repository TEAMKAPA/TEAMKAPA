import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  ClipboardList,
  Code2,
  Download,
  Eye,
  FileCheck2,
  FileText,
  KeyRound,
  Loader2,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Upload,
} from 'lucide-react';
import { getFieldTitle, type FormDefinition, type LeafField } from './types/coiForm';
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
import { Badge } from './components/ui/Badge';
import { Button } from './components/ui/Button';
import { Modal } from './components/ui/Modal';
import { FileDropzone } from './components/upload/FileDropzone';
import { BatchQueue, mergeJobSummaries } from './components/upload/BatchQueue';
import { SourcePreview } from './components/viewer/SourcePreview';
import { JsonViewer } from './components/viewer/JsonViewer';
import { FormRenderer } from './components/renderer/FormRenderer';
import { QualityReportPanel } from './components/audit/QualityReportPanel';

type TopView = 'upload' | 'review';
type ReviewTab = 'fields' | 'interactive' | 'report' | 'json';

function cloneForm(form: FormDefinition): FormDefinition {
  return JSON.parse(JSON.stringify(form)) as FormDefinition;
}

function findFieldById(
  form: FormDefinition,
  fieldId: string,
): { tabId: string; field: LeafField } | null {
  for (const tab of form.tabs) {
    for (const sq of tab.questions) {
      for (const fq of sq.field.repetingSectionGrid) {
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
    tabs: form.tabs.map((tab) => ({
      ...tab,
      questions: tab.questions.map((sq) => ({
        ...sq,
        field: {
          ...sq.field,
          repetingSectionGrid: sq.field.repetingSectionGrid.map((fq) =>
            fq.field.id === updated.id ? { ...fq, field: updated } : fq,
          ),
        },
      })),
    })),
  };
}

function randomHexId(): string {
  const arr = new Uint8Array(16);
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(arr);
  } else {
    for (let i = 0; i < arr.length; i += 1) arr[i] = Math.floor(Math.random() * 256);
  }
  return Array.from(arr, (b) => b.toString(16).padStart(2, '0')).join('');
}

export default function App() {
  const [topView, setTopView] = useState<TopView>('upload');
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

  const localFilesRef = useRef<Map<string, File>>(new Map());
  const [localFileVersion, setLocalFileVersion] = useState(0);

  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeFilename, setActiveFilename] = useState<string>('');
  const [originalResult, setOriginalResult] = useState<AnalysisResult | null>(null);
  const [editedForm, setEditedForm] = useState<FormDefinition | null>(null);
  const [activeTabId, setActiveTabId] = useState<string>('');
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [focusedSource, setFocusedSource] = useState<{
    source: FieldSource;
    label?: string;
  } | null>(null);

  const [reviewedIds, setReviewedIds] = useState<Set<string>>(new Set());
  const [modifiedIds, setModifiedIds] = useState<Set<string>>(new Set());
  const [appliedDictTechNames, setAppliedDictTechNames] = useState<Set<string>>(new Set());
  const [approval, setApproval] = useState<ApprovalView | null>(null);
  const [isApproving, setIsApproving] = useState(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ kind: 'success' | 'info' | 'error'; text: string } | null>(
    null,
  );

  const showToast = useCallback((kind: 'success' | 'info' | 'error', text: string) => {
    setToast({ kind, text });
  }, []);

  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => setToast(null), 4500);
    return () => window.clearTimeout(id);
  }, [toast]);

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
      jobId: string | null,
      filename: string,
      result: AnalysisResult,
      existingApproval?: ApprovalView | null,
    ) => {
      const formToEdit = existingApproval ? cloneForm(existingApproval.form) : cloneForm(result.form);
      setActiveJobId(jobId);
      setActiveFilename(filename);
      setOriginalResult(result);
      setEditedForm(formToEdit);
      setActiveTabId(formToEdit.tabs[0]?.id ?? '');
      setSelectedFieldId(null);
      setFocusedSource(null);
      setCurrentPage(1);
      setReviewedIds(new Set());
      setModifiedIds(new Set());
      setAppliedDictTechNames(new Set());
      setApproval(existingApproval ?? null);
      setApprovalError(null);
      setReviewTab('fields');
      setTopView('review');
    },
    [],
  );

  const handleLoadDemo = useCallback(() => {
    setDemoMode(true);
    setUploadError(null);
    setActiveBatch(MOCK_BATCH_VIEW);
    setJobs((prev) => {
      const exists = prev.some((j) => j.job_id === MOCK_DEMO_JOB_ID);
      if (exists) return prev;
      return [
        MOCK_JOB_VIEW,
        {
          ...MOCK_BATCH_VIEW.jobs[1],
          result: MOCK_ANALYSIS_RESULT,
        },
        ...prev,
      ];
    });
    openAnalysisResult(MOCK_DEMO_JOB_ID, MOCK_JOB_VIEW.filename, MOCK_ANALYSIS_RESULT);
    showToast('info', 'Załadowano przykładowy wniosek COI w trybie demonstracyjnym.');
  }, [openAnalysisResult, showToast]);

  const simulateDemoJob = useCallback((file: File, batchId: string | null = null): JobView => {
    const jobId = randomHexId();
    localFilesRef.current.set(jobId, file);
    setLocalFileVersion((v) => v + 1);

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
        message: 'Odczyt treści i obrazu dokumentu',
      },
    };

    window.setTimeout(() => {
      setJobs((prev) =>
        prev.map((j) =>
          j.job_id === jobId
            ? {
                ...j,
                progress: {
                  stage: 'fields',
                  step: 5,
                  total_steps: 7,
                  message: 'Ekstrakcja pól formularza',
                },
              }
            : j,
        ),
      );
    }, 650);

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
            counts: {
              QUEUED: 0,
              IN_PROGRESS: inProg,
              SUCCEEDED: succeeded,
              FAILED: 0,
            },
            jobs: nextJobs,
          };
        });
      }
    }, 1400);

    return initialJob;
  }, []);

  const handleSubmitSingle = async (file: File) => {
    setUploadError(null);
    setIsSubmitting(true);

    try {
      if (demoMode) {
        const simulated = simulateDemoJob(file);
        setJobs((prev) => [simulated, ...prev]);
        showToast('info', `Uruchomiono analizę demo dla „${file.name}”.`);
        return;
      }

      const response = await analyzeForm(file, { wait: 0 });
      if (response.kind === 'completed') {
        const tempId = randomHexId();
        localFilesRef.current.set(tempId, file);
        setLocalFileVersion((v) => v + 1);
        openAnalysisResult(null, file.name, response.data);
        showToast('success', `Analiza pliku „${file.name}” zakończona.`);
      } else {
        const accepted = response.data;
        localFilesRef.current.set(accepted.job_id, file);
        setLocalFileVersion((v) => v + 1);
        const newJob: JobView = {
          job_id: accepted.job_id,
          status: accepted.status,
          filename: accepted.filename,
          attempts: 0,
          created_at: new Date().toISOString(),
        };
        setJobs((prev) => [newJob, ...prev.filter((j) => j.job_id !== accepted.job_id)]);
        showToast('info', `Przyjęto plik „${accepted.filename}” do analizy.`);
      }
    } catch (err) {
      if (err instanceof ApiClientError) {
        if (err.status === 401) {
          setAuthModalOpen(true);
        }
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
          counts: {
            QUEUED: 0,
            IN_PROGRESS: createdJobs.length,
            SUCCEEDED: 0,
            FAILED: 0,
          },
          jobs: createdJobs,
          rejected: [],
        });
        showToast('info', `Uruchomiono paczkę demo (${files.length} plików).`);
        return;
      }

      const accepted = await analyzeBatch(files);
      const fileByName = new Map(files.map((f) => [f.name, f]));
      const now = new Date().toISOString();

      const newJobs: JobView[] = accepted.jobs.map((j) => {
        const matchedFile = fileByName.get(j.filename);
        if (matchedFile) {
          localFilesRef.current.set(j.job_id, matchedFile);
        }
        return {
          job_id: j.job_id,
          status: j.status,
          filename: j.filename,
          batch_id: accepted.batch_id,
          attempts: 0,
          created_at: now,
        };
      });
      setLocalFileVersion((v) => v + 1);

      setJobs((prev) => {
        const ids = new Set(newJobs.map((j) => j.job_id));
        return [...newJobs, ...prev.filter((j) => !ids.has(j.job_id))];
      });

      setActiveBatch({
        batch_id: accepted.batch_id,
        created_at: now,
        total_files: accepted.jobs.length + accepted.rejected.length,
        finished: false,
        counts: {
          QUEUED: accepted.jobs.length,
          IN_PROGRESS: 0,
          SUCCEEDED: 0,
          FAILED: 0,
        },
        jobs: newJobs,
        rejected: accepted.rejected,
      });

      showToast(
        'info',
        `Wysłano paczkę: przyjęto ${accepted.jobs.length}, odrzucono ${accepted.rejected.length}.`,
      );
    } catch (err) {
      if (err instanceof ApiClientError) {
        if (err.status === 401) {
          setAuthModalOpen(true);
        }
        setUploadError(`${err.code}: ${err.message}`);
      } else {
        setUploadError('Nieoczekiwany błąd podczas wysyłania paczki plików.');
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
    if (demoMode || (pendingJobIds.length === 0 && !activeBatchPendingId)) {
      return;
    }

    let cancelled = false;
    const poll = async () => {
      if (pendingJobIds.length > 0) {
        const updates = await Promise.allSettled(pendingJobIds.map((id) => getJob(id)));
        if (cancelled) return;
        const map = new Map<string, JobView>();
        for (const res of updates) {
          if (res.status === 'fulfilled') {
            map.set(res.value.job_id, res.value);
          }
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
          // Ponowienie przy kolejnym cyklu
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

    if (demoMode) {
      const result = existing?.result ?? MOCK_ANALYSIS_RESULT;
      openAnalysisResult(jobId, existing?.filename ?? MOCK_JOB_VIEW.filename, result);
      return;
    }

    try {
      const fullJob = existing?.result ? existing : await getJob(jobId);
      setJobs((prev) => prev.map((j) => (j.job_id === jobId ? fullJob : j)));
      if (!fullJob.result) {
        showToast('error', 'Zadanie nie zawiera jeszcze wyniku analizy.');
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
      openAnalysisResult(fullJob.job_id, fullJob.filename, fullJob.result, existingApproval);
    } catch (err) {
      if (err instanceof ApiClientError) {
        showToast('error', `${err.code}: ${err.message}`);
      } else {
        showToast('error', 'Nie udało się pobrać szczegółów zadania.');
      }
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
      showToast('error', 'Nie udało się pobrać wyniku JSON zadania.');
    }
  };

  const liveRuleIssues = useMemo(
    () => (editedForm ? validateFormDefinitionRules(editedForm) : []),
    [editedForm],
  );
  const liveErrors = useMemo(
    () => liveRuleIssues.filter((i) => i.severity === 'ERROR'),
    [liveRuleIssues],
  );

  const fieldLabels = useMemo(() => {
    const map = new Map<string, string>();
    if (!editedForm) return map;
    for (const tab of editedForm.tabs) {
      for (const sq of tab.questions) {
        for (const fq of sq.field.repetingSectionGrid) {
          map.set(fq.field.id, getFieldTitle(fq.field));
        }
      }
    }
    return map;
  }, [editedForm]);

  const handleSelectField = useCallback(
    (fieldId: string | null) => {
      setSelectedFieldId(fieldId);
      setFocusedSource(null);
      if (!fieldId) return;

      if (editedForm) {
        const located = findFieldById(editedForm, fieldId);
        if (located) {
          setActiveTabId(located.tabId);
        }
      }

      const assessment = originalResult?.report.fields.find((f) => f.field_id === fieldId);
      const firstPage = assessment?.sources.find((s) => s.page)?.page;
      if (firstPage) {
        setCurrentPage(firstPage);
      }
    },
    [editedForm, originalResult],
  );

  const handleFocusSource = useCallback((source: FieldSource, label?: string) => {
    setFocusedSource({ source, label });
    if (source.page) {
      setCurrentPage(source.page);
    }
  }, []);

  const handleUpdateField = (updated: LeafField) => {
    if (!editedForm) return;
    setEditedForm(replaceFieldInForm(editedForm, updated));
    setModifiedIds((prev) => new Set(prev).add(updated.id));
    setApprovalError(null);
  };

  const handleResetField = (fieldId: string) => {
    if (!originalResult || !editedForm) return;
    const orig = findFieldById(originalResult.form, fieldId);
    if (!orig) return;
    setEditedForm(replaceFieldInForm(editedForm, JSON.parse(JSON.stringify(orig.field)) as LeafField));
    setModifiedIds((prev) => {
      const next = new Set(prev);
      next.delete(fieldId);
      return next;
    });
  };

  const handleResetAllEdits = () => {
    if (!originalResult) return;
    setEditedForm(cloneForm(originalResult.form));
    setModifiedIds(new Set());
    setAppliedDictTechNames(new Set());
    setApprovalError(null);
    showToast('info', 'Przywrócono pierwotny wynik analizy.');
  };

  const handleToggleReviewed = (fieldId: string) => {
    setReviewedIds((prev) => {
      const next = new Set(prev);
      if (next.has(fieldId)) next.delete(fieldId);
      else next.add(fieldId);
      return next;
    });
  };

  const handleApplyDictionaryProposal = (proposal: DictionaryProposal) => {
    if (!editedForm) return;
    let matchedId: string | null = null;
    const nextForm: FormDefinition = {
      ...editedForm,
      tabs: editedForm.tabs.map((tab) => ({
        ...tab,
        questions: tab.questions.map((sq) => ({
          ...sq,
          field: {
            ...sq.field,
            repetingSectionGrid: sq.field.repetingSectionGrid.map((fq) => {
              if (fq.field.techName !== proposal.tech_name) return fq;
              matchedId = fq.field.id;
              const isReq =
                'validation' in fq.field &&
                (fq.field.validation as readonly string[]).includes('REQUIRED');
              const converted: LeafField = {
                id: fq.field.id,
                techName: fq.field.techName,
                questionOrder: fq.field.questionOrder,
                tooltipMessage: fq.field.tooltipMessage,
                type: 'SELECT',
                title: 'title' in fq.field ? fq.field.title : proposal.name,
                description: 'description' in fq.field ? fq.field.description : undefined,
                readOnly: 'readOnly' in fq.field ? fq.field.readOnly : undefined,
                searchEnabled: true,
                dictionaryName: proposal.name,
                options: [],
                validation: isReq ? ['REQUIRED'] : [],
                states: 'states' in fq.field ? fq.field.states : undefined,
              };
              return { ...fq, field: converted };
            }),
          },
        })),
      })),
    };

    if (matchedId) {
      setEditedForm(nextForm);
      setModifiedIds((prev) => new Set(prev).add(matchedId!));
      setAppliedDictTechNames((prev) => new Set(prev).add(proposal.tech_name));
      showToast('success', `Zastosowano słownik „${proposal.name}” dla pola ${proposal.tech_name}.`);
    } else {
      showToast('error', `Nie znaleziono pola „${proposal.tech_name}” w formularzu.`);
    }
  };

  const handleApproveForm = async () => {
    if (!editedForm) return;
    setApprovalError(null);

    if (liveErrors.length > 0) {
      setApprovalError(
        `Formularz zawiera ${liveErrors.length} błędów reguł COI poziomu ERROR. Popraw je przed zatwierdzeniem.`,
      );
      setReviewTab('report');
      return;
    }

    setIsApproving(true);
    try {
      const sanitized = sanitizeFormForExport(editedForm);
      if (demoMode || !activeJobId) {
        const now = new Date().toISOString();
        const mockApproval: ApprovalView = {
          job_id: activeJobId ?? MOCK_DEMO_JOB_ID,
          approved_by: 'urzednik (demo)',
          approved_at: now,
          form: sanitized,
          warnings: liveRuleIssues.filter((i) => i.severity !== 'ERROR'),
        };
        setApproval(mockApproval);
        setJobs((prev) =>
          prev.map((j) => (j.job_id === activeJobId ? { ...j, approved_at: now } : j)),
        );
        showToast('success', 'Formularz został zwalidowany i zatwierdzony (tryb demo).');
        return;
      }

      const saved = await approveForm(activeJobId, sanitized);
      setApproval(saved);
      setJobs((prev) =>
        prev.map((j) =>
          j.job_id === activeJobId ? { ...j, approved_at: saved.approved_at } : j,
        ),
      );
      showToast('success', 'Formularz zatwierdzony i zapisany w bazie COI.');
    } catch (err) {
      if (err instanceof ApiClientError) {
        if (err.status === 401) {
          setAuthModalOpen(true);
        }
        setApprovalError(`${err.code}: ${err.message}`);
      } else {
        setApprovalError('Nie udało się zapisać zatwierdzenia formularza.');
      }
    } finally {
      setIsApproving(false);
    }
  };

  const activeLocalFile = useMemo(() => {
    void localFileVersion;
    if (!activeJobId) return null;
    return localFilesRef.current.get(activeJobId) ?? null;
  }, [activeJobId, localFileVersion]);

  const isDemoDocument = Boolean(demoMode && activeJobId && !activeLocalFile);

  const handleAttachLocalFile = (file: File) => {
    const key = activeJobId ?? 'adhoc';
    localFilesRef.current.set(key, file);
    setLocalFileVersion((v) => v + 1);
    setCurrentPage(1);
    showToast('info', `Podłączono lokalny podgląd pliku „${file.name}”.`);
  };

  const handleSaveToken = () => {
    setStoredToken(tokenDraft);
    const cleaned = tokenDraft.trim();
    setJwtToken(cleaned);
    if (cleaned) setAuthRequired(false);
    setAuthModalOpen(false);
    showToast('info', cleaned ? 'Zapisano token JWT w sesji.' : 'Usunięto token JWT.');
  };

  return (
    <div className="flex min-h-screen flex-col bg-[#f4f6f9] text-slate-900">
      {/* Subtelny pasek akcentowy administracji publicznej */}
      <div className="h-1 w-full bg-gradient-to-r from-blue-950 via-blue-700 to-rose-600" />

      {/* Główny pasek nawigacji */}
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur-xs">
        <div className="mx-auto flex max-w-[1680px] flex-wrap items-center justify-between gap-4 px-5 py-3">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-900 text-white shadow-xs">
              <FileCheck2 className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-base font-bold tracking-tight text-slate-900">
                  COI Cyfryzacja formularzy
                </h1>
                <span className="hidden rounded-md bg-rose-600 px-2 py-0.5 text-[11px] font-bold tracking-wide text-white uppercase sm:inline">
                  TEAMKAPA
                </span>
              </div>
              <p className="text-xs text-slate-500">Panel weryfikacji e-formularzy dla urzędnika</p>
            </div>
          </div>

          <nav
            aria-label="Główna nawigacja"
            className="inline-flex rounded-xl border border-slate-200 bg-slate-100 p-1 text-sm"
          >
            <button
              type="button"
              onClick={() => setTopView('upload')}
              className={`inline-flex items-center gap-2 rounded-lg px-4 py-1.5 font-semibold transition-colors ${
                topView === 'upload'
                  ? 'bg-white text-blue-950 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <Upload className="h-4 w-4" />
              Przesyłanie i kolejka
              {pendingJobIds.length > 0 && <Badge variant="info">{pendingJobIds.length}</Badge>}
            </button>
            <button
              type="button"
              onClick={() => {
                if (editedForm) {
                  setTopView('review');
                } else {
                  handleLoadDemo();
                }
              }}
              className={`inline-flex items-center gap-2 rounded-lg px-4 py-1.5 font-semibold transition-colors ${
                topView === 'review'
                  ? 'bg-white text-blue-950 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <ClipboardList className="h-4 w-4" />
              Przegląd formularza
              {editedForm && originalResult && (
                <Badge variant={liveErrors.length > 0 ? 'danger' : 'warning'}>
                  {originalResult.report.fields.filter((f) => f.level !== 'HIGH').length}
                </Badge>
              )}
            </button>
          </nav>

          <div className="flex flex-wrap items-center gap-2.5">
            <button
              type="button"
              onClick={() => {
                setDemoMode((prev) => !prev);
                setUploadError(null);
              }}
              title="Przełącz między trybem Demo (offline) a żywym REST API"
              className={`inline-flex h-9 items-center gap-2 rounded-full border px-3.5 text-xs font-semibold transition-colors ${
                demoMode
                  ? 'border-amber-300 bg-amber-50 text-amber-950'
                  : apiStatus === 'online'
                    ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
                    : 'border-slate-200 bg-slate-100 text-slate-600'
              }`}
            >
              <span
                className={`h-2 w-2 rounded-full ${
                  demoMode
                    ? 'bg-amber-500'
                    : apiStatus === 'online'
                      ? 'bg-emerald-500'
                      : 'bg-slate-400'
                }`}
              />
              {demoMode
                ? 'Tryb Demo (offline)'
                : apiStatus === 'online'
                  ? 'API połączone'
                  : apiStatus === 'checking'
                    ? 'Sprawdzanie API…'
                    : 'API offline'}
            </button>

            <Button
              variant={jwtToken ? 'secondary' : 'ghost'}
              size="sm"
              onClick={() => {
                setTokenDraft(jwtToken);
                setAuthModalOpen(true);
              }}
            >
              <KeyRound className="h-4 w-4" />
              {jwtToken ? 'JWT aktywny' : 'Token JWT'}
            </Button>
          </div>
        </div>
      </header>

      {/* Powiadomienie toast */}
      {toast && (
        <div className="fixed right-5 bottom-5 z-50 max-w-md">
          <div
            role="status"
            className={`flex items-center gap-2.5 rounded-xl border px-4 py-3 text-sm font-medium shadow-xl ${
              toast.kind === 'success'
                ? 'border-emerald-200 bg-emerald-50 text-emerald-950'
                : toast.kind === 'error'
                  ? 'border-rose-200 bg-rose-50 text-rose-950'
                  : 'border-blue-200 bg-blue-50 text-blue-950'
            }`}
          >
            {toast.kind === 'success' ? (
              <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600" />
            ) : toast.kind === 'error' ? (
              <AlertCircle className="h-5 w-5 shrink-0 text-rose-600" />
            ) : (
              <Sparkles className="h-5 w-5 shrink-0 text-blue-600" />
            )}
            <span>{toast.text}</span>
          </div>
        </div>
      )}

      {/* Główna treść */}
      <main className="mx-auto w-full max-w-[1680px] flex-1 px-5 py-6">
        {topView === 'upload' && (
          <div className="mx-auto max-w-5xl space-y-6">
            {/* Zwięzły pasek kroków */}
            <div className="grid gap-3 sm:grid-cols-3">
              {[
                {
                  num: '1',
                  title: 'Prześlij blankiet',
                  desc: 'PDF, Word, arkusz lub skan — pojedynczo lub w paczce.',
                },
                {
                  num: '2',
                  title: 'Sprawdź niepewne pola',
                  desc: 'System podświetla źródło na stronie i ocenia pewność.',
                },
                {
                  num: '3',
                  title: 'Zatwierdź i pobierz JSON',
                  desc: 'Gotowy plik importu zgodny ze schematem COI.',
                },
              ].map((step) => (
                <div
                  key={step.num}
                  className="flex items-start gap-3 rounded-2xl border border-slate-200/80 bg-white px-4 py-3.5"
                >
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-blue-50 text-xs font-bold text-blue-700">
                    {step.num}
                  </span>
                  <div>
                    <div className="text-sm font-semibold text-slate-900">{step.title}</div>
                    <p className="mt-0.5 text-xs text-slate-500">{step.desc}</p>
                  </div>
                </div>
              ))}
            </div>

            {apiStatus === 'online' && authRequired && !demoMode && (
              <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-blue-200 bg-blue-50/80 px-5 py-4 text-sm text-blue-950">
                <div className="flex items-center gap-3">
                  <KeyRound className="h-5 w-5 shrink-0 text-blue-700" />
                  <span>
                    Backend API wymaga uwierzytelnienia <strong>JWT Bearer</strong>. Wklej token z{' '}
                    <code>token_cli</code> lub sprawdź aplikację w Trybie Demo.
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => {
                      setTokenDraft(jwtToken);
                      setAuthModalOpen(true);
                    }}
                  >
                    <KeyRound className="h-4 w-4" />
                    Wklej token JWT
                  </Button>
                  <Button variant="secondary" size="sm" onClick={handleLoadDemo}>
                    <Sparkles className="h-4 w-4 text-amber-500" />
                    Tryb Demo
                  </Button>
                </div>
              </div>
            )}

            {apiStatus === 'offline' && !demoMode && (
              <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-amber-200 bg-amber-50 px-5 py-4 text-sm text-amber-950">
                <div className="flex items-center gap-3">
                  <AlertCircle className="h-5 w-5 shrink-0 text-amber-600" />
                  <span>
                    Backend API jest aktualnie niedostępny. Możesz uruchomić Docker Compose albo
                    przetestować pełny interfejs offline w <strong>Trybie Demo</strong>.
                  </span>
                </div>
                <Button variant="secondary" size="sm" onClick={handleLoadDemo}>
                  <Sparkles className="h-4 w-4 text-amber-500" />
                  Uruchom Tryb Demo
                </Button>
              </div>
            )}

            <FileDropzone
              onSubmitSingle={handleSubmitSingle}
              onSubmitBatch={handleSubmitBatch}
              onLoadDemo={handleLoadDemo}
              isSubmitting={isSubmitting}
              demoMode={demoMode}
              apiError={uploadError}
              onDismissError={() => setUploadError(null)}
            />

            <BatchQueue
              jobs={jobs}
              activeBatch={activeBatch}
              selectedJobId={activeJobId}
              statusFilter={statusFilter}
              onStatusFilterChange={setStatusFilter}
              onSelectJob={handleSelectJob}
              onDownloadJobJson={handleDownloadJobJson}
              onRefresh={refreshJobsList}
              isRefreshing={isRefreshing}
            />
          </div>
        )}

        {topView === 'review' && editedForm && originalResult && (
          <div className="space-y-5">
            {/* Jeden zunifikowany pasek roboczy przeglądu (zamiast 3 piętrowych pasków) */}
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="min-w-0 flex-1 space-y-1">
                  <label className="sr-only" htmlFor="coi-form-title-input">
                    Tytuł formularza COI
                  </label>
                  <input
                    id="coi-form-title-input"
                    type="text"
                    value={editedForm.title}
                    onChange={(e) =>
                      setEditedForm((prev) => (prev ? { ...prev, title: e.target.value } : prev))
                    }
                    className="w-full rounded-lg border border-transparent bg-transparent px-1.5 py-0.5 text-lg font-bold text-slate-900 hover:border-slate-200 focus:border-blue-600 focus:bg-white focus:outline-none"
                  />
                  <div className="flex flex-wrap items-center gap-2 px-1.5">
                    {approval ? (
                      <Badge variant="success">
                        <ShieldCheck className="h-3.5 w-3.5" />
                        Zatwierdzono ({approval.approved_by})
                      </Badge>
                    ) : liveErrors.length > 0 ? (
                      <Badge variant="danger">
                        <ShieldAlert className="h-3.5 w-3.5" />
                        {liveErrors.length} błędów blokuje zatwierdzenie
                      </Badge>
                    ) : (
                      <Badge variant="info">Gotowy do zatwierdzenia</Badge>
                    )}
                    {modifiedIds.size > 0 && (
                      <Badge variant="warning">Zmodyfikowane pola: {modifiedIds.size}</Badge>
                    )}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  {modifiedIds.size > 0 && (
                    <Button variant="ghost" size="sm" onClick={handleResetAllEdits}>
                      <RotateCcw className="h-4 w-4" />
                      Cofnij poprawki
                    </Button>
                  )}
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => downloadFormJson(editedForm, activeFilename)}
                  >
                    <Download className="h-4 w-4" />
                    Pobierz JSON
                  </Button>
                  <Button
                    variant="success"
                    size="sm"
                    onClick={handleApproveForm}
                    disabled={isApproving || liveErrors.length > 0}
                    data-testid="approve-form-button"
                  >
                    {isApproving ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Zatwierdzanie…
                      </>
                    ) : (
                      <>
                        <ShieldCheck className="h-4 w-4" />
                        {approval ? 'Zapisz ponownie' : 'Zatwierdź formularz'}
                      </>
                    )}
                  </Button>
                </div>
              </div>

              {/* Zunifikowany przełącznik 4 widoków przeglądu */}
              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
                {(
                  [
                    {
                      id: 'fields',
                      label: `Pola do weryfikacji (${originalResult.report.fields.length})`,
                      icon: FileText,
                    },
                    { id: 'interactive', label: 'Podgląd wypełniania', icon: Eye },
                    { id: 'report', label: 'Raport jakości', icon: ShieldCheck },
                    { id: 'json', label: 'Podgląd JSON', icon: Code2 },
                  ] as const
                ).map(({ id, label, icon: Icon }) => {
                  const active = reviewTab === id;
                  return (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setReviewTab(id)}
                      className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold transition-colors ${
                        active
                          ? 'bg-blue-50 text-blue-800 ring-1 ring-blue-200'
                          : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                      }`}
                    >
                      <Icon className="h-4 w-4" />
                      {label}
                      {id === 'report' && liveErrors.length > 0 && (
                        <Badge variant="danger">{liveErrors.length}</Badge>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>

            {approvalError && (
              <div
                role="alert"
                className="flex items-center gap-2.5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900"
              >
                <AlertCircle className="h-5 w-5 shrink-0 text-rose-600" />
                <span>{approvalError}</span>
              </div>
            )}

            {approval && approval.warnings.length > 0 && (
              <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
                <strong>Zatwierdzono z ostrzeżeniami ({approval.warnings.length}):</strong>{' '}
                {approval.warnings.map((w) => w.message).join(' · ')}
              </div>
            )}

            {/* Split-view z wygodnym odstępem */}
            <div className="grid items-start gap-6 lg:grid-cols-[minmax(400px,45%)_1fr]">
              <div className="lg:sticky lg:top-20 lg:h-[calc(100vh-6.5rem)]">
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
                  onSelectField={(id) => {
                    handleSelectField(id);
                    setReviewTab('fields');
                  }}
                  onAttachLocalFile={handleAttachLocalFile}
                  fieldLabels={fieldLabels}
                />
              </div>

              <div className="min-w-0">
                {(reviewTab === 'fields' || reviewTab === 'interactive') && (
                  <FormRenderer
                    mode={reviewTab === 'interactive' ? 'interactive' : 'review'}
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
                    onFocusSelectedOnDocument={() => {
                      if (!selectedFieldId) return;
                      const a = originalResult.report.fields.find(
                        (f) => f.field_id === selectedFieldId,
                      );
                      const src = a?.sources.find((s) => s.page);
                      if (src?.page) setCurrentPage(src.page);
                    }}
                  />
                )}

                {reviewTab === 'report' && (
                  <QualityReportPanel
                    report={originalResult.report}
                    liveRuleIssues={liveRuleIssues}
                    appliedDictionaryTechNames={appliedDictTechNames}
                    onSelectField={(fieldId) => {
                      handleSelectField(fieldId);
                      setReviewTab('fields');
                    }}
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
            </div>
          </div>
        )}
      </main>

      {/* Modal konfiguracji tokenu JWT */}
      <Modal
        open={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        title="Uwierzytelnianie REST API (JWT)"
        subtitle="Token zapisywany jest wyłącznie w pamięci bieżącej karty przeglądarki (sessionStorage)."
      >
        <div className="space-y-4 text-sm">
          <div className="rounded-xl bg-slate-50 p-4 text-slate-600">
            <p className="font-semibold text-slate-900">Wygeneruj token komendą w katalogu TEAMKAPA:</p>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-900 p-3 font-mono text-xs text-slate-100">
              docker compose exec -T api python -m backend.api.token_cli --sub urzednik --minutes 480
            </pre>
            <p className="mt-2 text-xs text-slate-500">
              Jeśli backend pracuje z <code>JWT_ENABLED=false</code>, pole może pozostać puste.
            </p>
          </div>

          <label className="block space-y-1.5">
            <span className="font-medium text-slate-800">Token JWT</span>
            <textarea
              rows={3}
              value={tokenDraft}
              onChange={(e) => setTokenDraft(e.target.value)}
              placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
              className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 font-mono text-xs focus:border-blue-600 focus:ring-2 focus:ring-blue-600/15 focus:outline-none"
            />
          </label>

          <div className="flex items-center justify-between gap-2 pt-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setTokenDraft('');
                setStoredToken('');
                setJwtToken('');
              }}
            >
              Wyczyść token
            </Button>
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" onClick={() => setAuthModalOpen(false)}>
                Anuluj
              </Button>
              <Button variant="primary" size="sm" onClick={handleSaveToken}>
                Zapisz token
              </Button>
            </div>
          </div>
        </div>
      </Modal>
    </div>
  );
}
