/**
 * Walidacja przesyłanych plików po stronie klienta — 1:1 z backend/api/file_validators.py.
 */

export const MAX_FILENAME_LENGTH = 250;
export const DEFAULT_MAX_FILE_SIZE_MB = 20;
export const DEFAULT_MAX_BATCH_FILES = 20;

export const ALLOWED_EXTENSIONS = [
  'pdf',
  'doc',
  'docx',
  'ods',
  'xlsx',
  'jpg',
  'jpeg',
  'png',
] as const;

const ALLOWED_EXTENSION_SET: ReadonlySet<string> = new Set(ALLOWED_EXTENSIONS);

export const FORBIDDEN_FILENAME_CHARS = new Set([
  ';',
  ':',
  '>',
  '<',
  '\\',
  '/',
  '|',
  "'",
  '*',
  '%',
  '$',
]);

export const ACCEPT_ATTRIBUTE =
  '.pdf,.doc,.docx,.ods,.xlsx,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png';

export type FileValidationErrorCode =
  | 'FILENAME_MISSING'
  | 'FILENAME_TOO_LONG'
  | 'FILENAME_FORBIDDEN_CHARS'
  | 'EXTENSION_NOT_ALLOWED'
  | 'FILE_EMPTY'
  | 'FILE_TOO_LARGE'
  | 'TOO_MANY_FILES';

export interface FileValidationIssue {
  code: FileValidationErrorCode;
  message: string;
}

export interface FileValidationResult {
  file: File;
  valid: boolean;
  extension: string | null;
  error?: FileValidationIssue;
}

export interface BatchValidationResult {
  validFiles: File[];
  invalidFiles: Array<{ file: File; error: FileValidationIssue }>;
  batchError?: FileValidationIssue;
}

export interface ValidationOptions {
  maxFileSizeMb?: number;
  maxBatchFiles?: number;
}

export function extractExtension(filename: string): { stem: string; extension: string } | null {
  const lastDot = filename.lastIndexOf('.');
  if (lastDot <= 0 || lastDot === filename.length - 1) return null;
  const stem = filename.slice(0, lastDot);
  const extension = filename.slice(lastDot + 1).toLowerCase();
  if (!stem.trim()) return null;
  return { stem, extension };
}

export function validateFilename(filename: string): { extension: string } | FileValidationIssue {
  if (!filename || !filename.trim()) {
    return { code: 'FILENAME_MISSING', message: 'Brak nazwy pliku' };
  }
  if (filename.length > MAX_FILENAME_LENGTH) {
    return {
      code: 'FILENAME_TOO_LONG',
      message: `Nazwa pliku ma ${filename.length} znaków (maksymalnie ${MAX_FILENAME_LENGTH})`,
    };
  }
  const badChars = new Set<string>();
  for (const ch of filename) {
    const code = ch.charCodeAt(0);
    if (FORBIDDEN_FILENAME_CHARS.has(ch) || code < 32 || code === 0x7f) {
      badChars.add(ch);
    }
  }
  if (badChars.size > 0) {
    const shown = Array.from(badChars)
      .sort()
      .map((c) => `'${c}'`)
      .join(' ');
    return {
      code: 'FILENAME_FORBIDDEN_CHARS',
      message: `Nazwa pliku zawiera niedozwolone znaki: ${shown}`,
    };
  }
  const parsed = extractExtension(filename);
  if (!parsed || !ALLOWED_EXTENSION_SET.has(parsed.extension)) {
    return {
      code: 'EXTENSION_NOT_ALLOWED',
      message: `Niedozwolony typ pliku. Dozwolone rozszerzenia: ${ALLOWED_EXTENSIONS.join(', ')}`,
    };
  }
  return { extension: parsed.extension };
}

export function validateFile(file: File, options: ValidationOptions = {}): FileValidationResult {
  const maxMb = options.maxFileSizeMb ?? DEFAULT_MAX_FILE_SIZE_MB;
  const maxBytes = maxMb * 1024 * 1024;

  const nameCheck = validateFilename(file.name);
  if ('code' in nameCheck) {
    return { file, valid: false, extension: null, error: nameCheck };
  }
  if (file.size === 0) {
    return {
      file,
      valid: false,
      extension: nameCheck.extension,
      error: { code: 'FILE_EMPTY', message: 'Plik jest pusty' },
    };
  }
  if (file.size > maxBytes) {
    return {
      file,
      valid: false,
      extension: nameCheck.extension,
      error: {
        code: 'FILE_TOO_LARGE',
        message: `Plik jest za duży (${formatBytes(file.size)}, maksymalnie ${maxMb} MB)`,
      },
    };
  }
  return { file, valid: true, extension: nameCheck.extension };
}

export function validateBatchFiles(
  files: File[],
  options: ValidationOptions = {},
): BatchValidationResult {
  const maxBatchFiles = options.maxBatchFiles ?? DEFAULT_MAX_BATCH_FILES;
  const batchError: FileValidationIssue | undefined =
    files.length > maxBatchFiles
      ? {
          code: 'TOO_MANY_FILES',
          message: `Za dużo plików w paczce: ${files.length} (maksymalnie ${maxBatchFiles})`,
        }
      : undefined;

  const validFiles: File[] = [];
  const invalidFiles: Array<{ file: File; error: FileValidationIssue }> = [];

  for (const file of files) {
    const result = validateFile(file, options);
    if (result.valid) validFiles.push(file);
    else if (result.error) invalidFiles.push({ file, error: result.error });
  }

  return { validFiles, invalidFiles, batchError };
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}
