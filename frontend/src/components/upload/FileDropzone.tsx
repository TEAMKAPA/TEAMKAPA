import { useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  File,
  FileSpreadsheet,
  FileText,
  Image as ImageIcon,
  Loader2,
  Sparkles,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import {
  ACCEPT_ATTRIBUTE,
  ALLOWED_EXTENSIONS,
  DEFAULT_MAX_BATCH_FILES,
  DEFAULT_MAX_FILE_SIZE_MB,
  formatBytes,
  validateBatchFiles,
  validateFile,
  type FileValidationResult,
} from '../../lib/fileValidation';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card, CardHeader } from '../ui/Card';

interface FileDropzoneProps {
  onSubmitSingle: (file: File) => Promise<void>;
  onSubmitBatch: (files: File[]) => Promise<void>;
  onLoadDemo: () => void;
  isSubmitting: boolean;
  demoMode: boolean;
  apiError?: string | null;
  onDismissError?: () => void;
}

function fileIcon(ext: string | null) {
  switch (ext) {
    case 'pdf':
      return <FileText className="h-5 w-5 shrink-0 text-rose-600" />;
    case 'doc':
    case 'docx':
      return <FileText className="h-5 w-5 shrink-0 text-blue-600" />;
    case 'xlsx':
    case 'ods':
      return <FileSpreadsheet className="h-5 w-5 shrink-0 text-emerald-600" />;
    case 'jpg':
    case 'jpeg':
    case 'png':
      return <ImageIcon className="h-5 w-5 shrink-0 text-indigo-600" />;
    default:
      return <File className="h-5 w-5 shrink-0 text-slate-400" />;
  }
}

export function FileDropzone({
  onSubmitSingle,
  onSubmitBatch,
  onLoadDemo,
  isSubmitting,
  demoMode,
  apiError,
  onDismissError,
}: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragActive, setIsDragActive] = useState(false);
  const [selected, setSelected] = useState<FileValidationResult[]>([]);

  const addFiles = (incoming: FileList | File[]) => {
    const arr = Array.from(incoming);
    if (arr.length === 0) return;

    setSelected((prev) => {
      const existingKeys = new Set(
        prev.map((p) => `${p.file.name}:${p.file.size}:${p.file.lastModified}`),
      );
      const next = [...prev];
      for (const f of arr) {
        const key = `${f.name}:${f.size}:${f.lastModified}`;
        if (!existingKeys.has(key)) {
          next.push(validateFile(f));
          existingKeys.add(key);
        }
      }
      return next;
    });
  };

  const handleFileInputChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      addFiles(e.target.files);
      e.target.value = '';
    }
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragActive(false);
    if (e.dataTransfer.files) {
      addFiles(e.dataTransfer.files);
    }
  };

  const removeFile = (index: number) => {
    setSelected((prev) => prev.filter((_, idx) => idx !== index));
  };

  const clearAll = () => {
    setSelected([]);
    onDismissError?.();
  };

  const batchCheck = validateBatchFiles(selected.map((s) => s.file));
  const validFiles = batchCheck.validFiles;
  const invalidCount = batchCheck.invalidFiles.length;
  const hasBatchLimitError = Boolean(batchCheck.batchError);

  const handleSubmit = async () => {
    if (validFiles.length === 0 || hasBatchLimitError || isSubmitting) return;
    if (selected.length === 1 && validFiles.length === 1) {
      await onSubmitSingle(validFiles[0]);
    } else {
      await onSubmitBatch(validFiles);
    }
    setSelected([]);
  };

  return (
    <Card>
      <CardHeader
        title="Prześlij blankiet urzędowy"
        subtitle={`Pojedynczy dokument lub paczka do ${DEFAULT_MAX_BATCH_FILES} plików (maks. ${DEFAULT_MAX_FILE_SIZE_MB} MB / plik)`}
        action={
          <Button variant="secondary" size="sm" onClick={onLoadDemo}>
            <Sparkles className="h-4 w-4 text-amber-500" />
            Załaduj przykład demo
          </Button>
        }
      />

      <div className="space-y-5 p-6">
        {apiError && (
          <div
            role="alert"
            className="flex items-start justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900"
          >
            <div className="flex items-start gap-2.5">
              <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-rose-600" />
              <div>
                <div className="font-semibold">Nie udało się wysłać dokumentu</div>
                <div className="mt-0.5 text-rose-800">{apiError}</div>
              </div>
            </div>
            {onDismissError && (
              <button
                type="button"
                onClick={onDismissError}
                aria-label="Ukryj komunikat błędu"
                className="rounded-lg p-1 text-rose-500 hover:bg-rose-100"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        )}

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragActive(true);
          }}
          onDragLeave={() => setIsDragActive(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          aria-label="Obszar przeciągania i wyboru plików formularzy"
          className={`flex flex-col items-center justify-center rounded-2xl border-2 border-dashed px-8 py-12 text-center transition-all ${
            isDragActive
              ? 'border-blue-600 bg-blue-50/80 ring-4 ring-blue-100'
              : 'border-slate-300 bg-slate-50/70 hover:border-blue-500 hover:bg-blue-50/30'
          }`}
        >
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-100 text-blue-700 shadow-2xs">
            <Upload className="h-7 w-7" />
          </div>
          <p className="mt-4 text-base font-semibold text-slate-900">
            Przeciągnij i upuść pliki tutaj albo kliknij, aby wybrać z dysku
          </p>
          <p className="mt-1.5 max-w-lg text-sm text-slate-500">
            Obsługiwane formaty: {ALLOWED_EXTENSIONS.map((e) => e.toUpperCase()).join(', ')}. Pliki
            nie są utrwalane na serwerze — podgląd stron działa lokalnie w przeglądarce.
          </p>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPT_ATTRIBUTE}
            onChange={handleFileInputChange}
            className="hidden"
            data-testid="file-upload-input"
          />
        </div>

        {batchCheck.batchError && (
          <div
            role="alert"
            className="flex items-center gap-2.5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900"
          >
            <AlertCircle className="h-5 w-5 shrink-0 text-rose-600" />
            <span>{batchCheck.batchError.message}</span>
          </div>
        )}

        {selected.length > 0 && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-semibold text-slate-900">
                  Wybrane pliki ({selected.length})
                </span>
                <Badge variant="success">{validFiles.length} poprawnych</Badge>
                {invalidCount > 0 && <Badge variant="danger">{invalidCount} odrzuconych</Badge>}
              </div>
              <Button variant="ghost" size="xs" onClick={clearAll} disabled={isSubmitting}>
                <Trash2 className="h-3.5 w-3.5" />
                Wyczyść listę
              </Button>
            </div>

            <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
              {selected.map((item, idx) => (
                <li
                  key={`${item.file.name}-${item.file.size}-${idx}`}
                  className="flex items-center justify-between gap-4 px-4 py-3 text-sm"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    {fileIcon(item.extension)}
                    <div className="min-w-0">
                      <div className="truncate font-medium text-slate-900">{item.file.name}</div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-2.5 text-xs">
                        <span className="text-slate-500 tabular-nums">
                          {formatBytes(item.file.size)}
                        </span>
                        {item.valid ? (
                          <span className="inline-flex items-center gap-1 font-medium text-emerald-700">
                            <CheckCircle2 className="h-3.5 w-3.5" />
                            Gotowy do analizy
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 font-medium text-rose-700">
                            <AlertCircle className="h-3.5 w-3.5" />
                            {item.error?.message}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeFile(idx)}
                    disabled={isSubmitting}
                    aria-label={`Usuń plik ${item.file.name}`}
                    className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-40"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </li>
              ))}
            </ul>

            <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3.5">
              <div className="text-sm text-slate-600">
                {demoMode ? (
                  <span>
                    Aktywny <strong>Tryb Demo</strong> — analiza zostanie zasymulowana lokalnie.
                  </span>
                ) : (
                  <span>Gotowe do wysłania do kolejki analizy COI.</span>
                )}
              </div>
              <Button
                variant="primary"
                size="md"
                onClick={handleSubmit}
                disabled={validFiles.length === 0 || hasBatchLimitError || isSubmitting}
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Wysyłanie…
                  </>
                ) : validFiles.length === 1 && selected.length === 1 ? (
                  <>
                    <Upload className="h-4 w-4" />
                    Rozpocznij analizę
                  </>
                ) : (
                  <>
                    <Upload className="h-4 w-4" />
                    Uruchom paczkę ({validFiles.length}{' '}
                    {validFiles.length === 1 ? 'plik' : validFiles.length < 5 ? 'pliki' : 'plików'})
                  </>
                )}
              </Button>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
