import { useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import {
  ACCEPT_ATTRIBUTE,
  DEFAULT_MAX_BATCH_FILES,
  DEFAULT_MAX_FILE_SIZE_MB,
  formatBytes,
  validateBatchFiles,
  validateFile,
  type FileValidationResult,
} from '../../lib/fileValidation';

interface FileDropzoneProps {
  onSubmitSingle: (file: File) => Promise<void>;
  onSubmitBatch: (files: File[]) => Promise<void>;
  onLoadDemo: () => void;
  isSubmitting: boolean;
  apiError?: string | null;
  onDismissError?: () => void;
}

/**
 * Strefa wgrywania plików — pojedynczy płaski biały panel (#FFFFFF, 1px solid #E5E7EB, rounded-lg)
 * bez zagnieżdżonych boksów w środku.
 */
export function FileDropzone({
  onSubmitSingle,
  onSubmitBatch,
  onLoadDemo,
  isSubmitting,
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

  const handleDrop = (e: DragEvent<HTMLElement>) => {
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
    <section
      id="przesylanie"
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragActive(true);
      }}
      onDragLeave={() => setIsDragActive(false)}
      onDrop={handleDrop}
      className={`rounded-lg border bg-[#FFFFFF] shadow-none transition-colors ${
        isDragActive ? 'border-[#08255E]' : 'border-[#E5E7EB]'
      }`}
    >
      {/* Górna belka panelu */}
      <div className="flex flex-col gap-3 border-b border-[#E5E7EB] px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <h2 className="text-[22px] font-normal leading-7 text-[#0D0E0E]">
          Wgrywanie plików
        </h2>
        <button
          type="button"
          onClick={onLoadDemo}
          className="w-full rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-4 py-2 text-[15px] font-normal text-[#0D0E0E] hover:bg-[#F3F3F4] sm:w-auto"
        >
          Załaduj przykład
        </button>
      </div>

      {apiError && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 border-b border-[#E5E7EB] px-6 py-3 text-[15px] font-normal text-[#CA0049]"
        >
          <span>Nie udało się wysłać pliku: {apiError}</span>
          {onDismissError && (
            <button
              type="button"
              onClick={onDismissError}
              className="text-[15px] font-normal text-[#CA0049] underline"
            >
              Zamknij
            </button>
          )}
        </div>
      )}

      {/* Obszar kliknięcia i upuszczania bez wewnętrznej ramki */}
      <div
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
        className="cursor-pointer px-6 py-10 text-center transition-colors hover:bg-[#F3F3F4]/40"
      >
        <p className="text-[17px] font-normal text-[#0D0E0E]">
          Przeciągnij pliki tutaj lub kliknij, aby wybrać z dysku
        </p>
        <p className="mt-2 text-[15px] font-normal text-[#374151]">
          Obsługiwane formaty: PDF, DOC, DOCX, ODS, XLSX, JPG, PNG (maksymalnie{' '}
          {DEFAULT_MAX_FILE_SIZE_MB} MB na plik, do {DEFAULT_MAX_BATCH_FILES} plików)
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
          className="border-t border-[#E5E7EB] px-6 py-3 text-sm font-normal text-[#CA0049]"
        >
          {batchCheck.batchError.message}
        </div>
      )}

      {/* Wybrane pliki oddzielone tylko cienkimi liniami */}
      {selected.length > 0 && (
        <div className="border-t border-[#E5E7EB]">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#E5E7EB] px-6 py-2.5 text-sm font-normal text-[#52555A]">
            <span>
              Wybrane pliki: {selected.length} (poprawnych: {validFiles.length}
              {invalidCount > 0 ? `, odrzuconych: ${invalidCount}` : ''})
            </span>
            <button
              type="button"
              onClick={clearAll}
              disabled={isSubmitting}
              className="text-sm font-normal text-[#08255E] hover:underline disabled:opacity-40"
            >
              Wyczyść listę
            </button>
          </div>

          <ul className="divide-y divide-[#E5E7EB]">
            {selected.map((item, idx) => (
              <li
                key={`${item.file.name}-${item.file.size}-${idx}`}
                className="flex items-center justify-between gap-4 px-6 py-3 text-sm font-normal"
              >
                <div className="min-w-0">
                  <div className="truncate text-[#0D0E0E]">{item.file.name}</div>
                  <div className="mt-0.5 text-xs font-normal text-[#52555A]">
                    <span>{formatBytes(item.file.size)}</span>
                    {' · '}
                    {item.valid ? (
                      <span className="text-[#065F46]">Gotowy do wysłania</span>
                    ) : (
                      <span className="text-[#CA0049]">{item.error?.message}</span>
                    )}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => removeFile(idx)}
                  disabled={isSubmitting}
                  className="shrink-0 text-sm font-normal text-[#52555A] hover:text-[#0D0E0E] disabled:opacity-40"
                >
                  Usuń
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Dolny pasek z głównym przyciskiem */}
      <div className="flex flex-col gap-3 border-t border-[#E5E7EB] px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <span className="text-[15px] font-normal text-[#374151]">
          {validFiles.length > 0
            ? `Gotowych do wysłania: ${validFiles.length}`
            : 'Wybierz co najmniej jeden plik, aby rozpocząć analizę.'}
        </span>

        <button
          type="button"
          onClick={handleSubmit}
          disabled={validFiles.length === 0 || hasBatchLimitError || isSubmitting}
          className="w-full rounded-lg bg-[#CA0049] px-6 py-3 text-[16px] font-normal text-[#FCE6EE] shadow-none transition-colors hover:bg-[#A8003D] disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
        >
          {isSubmitting ? 'Wysyłanie...' : 'Prześlij pliki'}
        </button>
      </div>
    </section>
  );
}
