import { describe, expect, it } from 'vitest';
import {
  DEFAULT_MAX_BATCH_FILES,
  MAX_FILENAME_LENGTH,
  validateBatchFiles,
  validateFile,
  validateFilename,
} from '../lib/fileValidation';

function makeFile(name: string, sizeBytes = 1024, type = 'application/pdf'): File {
  const blob = new Blob([new Uint8Array(Math.min(sizeBytes, 64))], { type });
  const file = new File([blob], name, { type });
  if (sizeBytes > 64) {
    Object.defineProperty(file, 'size', { value: sizeBytes });
  }
  return file;
}

describe('fileValidation (zgodność z backend/api/file_validators.py)', () => {
  it('akceptuje dozwolone rozszerzenia i normalizuje wielkość liter', () => {
    expect(validateFilename('wniosek.PDF')).toEqual({ extension: 'pdf' });
    expect(validateFilename('zalacznik.docx')).toEqual({ extension: 'docx' });
    expect(validateFilename('skan_01.JPEG')).toEqual({ extension: 'jpeg' });
  });

  it('odrzuca niedozwolone rozszerzenia, puste nazwy i zakazane znaki', () => {
    expect(validateFilename('')).toMatchObject({ code: 'FILENAME_MISSING' });
    expect(validateFilename('archiwum.zip')).toMatchObject({ code: 'EXTENSION_NOT_ALLOWED' });
    expect(validateFilename('wniosek;rm.pdf')).toMatchObject({ code: 'FILENAME_FORBIDDEN_CHARS' });
    expect(validateFilename(`${'a'.repeat(MAX_FILENAME_LENGTH)}.pdf`)).toMatchObject({
      code: 'FILENAME_TOO_LONG',
    });
  });

  it('wykrywa pusty lub zbyt duży plik', () => {
    const empty = makeFile('pusty.pdf', 0);
    expect(validateFile(empty).error?.code).toBe('FILE_EMPTY');

    const huge = makeFile('duzy.pdf', 25 * 1024 * 1024);
    expect(validateFile(huge, { maxFileSizeMb: 20 }).error?.code).toBe('FILE_TOO_LARGE');
  });

  it('waliduje paczki wsadowe i limit liczby plików', () => {
    const good = makeFile('ok.pdf', 2048);
    const bad = makeFile('zly.exe', 2048);
    const res = validateBatchFiles([good, bad]);
    expect(res.validFiles).toHaveLength(1);
    expect(res.invalidFiles).toHaveLength(1);
    expect(res.batchError).toBeUndefined();

    const tooMany = Array.from({ length: DEFAULT_MAX_BATCH_FILES + 1 }, (_, i) =>
      makeFile(`f_${i}.pdf`, 512),
    );
    const over = validateBatchFiles(tooMany);
    expect(over.batchError?.code).toBe('TOO_MANY_FILES');
  });
});
