import { describe, expect, it } from 'vitest';
import {
  DEFAULT_MAX_BATCH_FILES,
  DEFAULT_MAX_FILE_SIZE_MB,
  MAX_FILENAME_LENGTH,
  validateBatchFiles,
  validateFile,
  validateFilename,
} from '../lib/fileValidation';

function makeFile(name: string, sizeBytes = 1024, type = 'application/pdf'): File {
  const blob = new Blob([new Uint8Array(sizeBytes)], { type });
  return new File([blob], name, { type });
}

describe('fileValidation', () => {
  it('akceptuje wszystkie dozwolone rozszerzenia', () => {
    for (const ext of ['pdf', 'doc', 'docx', 'ods', 'xlsx', 'jpg', 'jpeg', 'png']) {
      const res = validateFile(makeFile(`formularz.${ext}`, 512));
      expect(res.valid).toBe(true);
      expect(res.extension).toBe(ext);
    }
  });

  it('odrzuca niedozwolone rozszerzenie i brak rozszerzenia', () => {
    expect(validateFile(makeFile('archiwum.zip')).error?.code).toBe('EXTENSION_NOT_ALLOWED');
    expect(validateFile(makeFile('skrypt.exe')).error?.code).toBe('EXTENSION_NOT_ALLOWED');
    expect(validateFile(makeFile('bez_rozszerzenia')).error?.code).toBe('EXTENSION_NOT_ALLOWED');
  });

  it('odrzuca nazwy z niedozwolonymi znakami oraz zbyt długie nazwy', () => {
    expect(validateFilename('wniosek;1.pdf')).toMatchObject({ code: 'FILENAME_FORBIDDEN_CHARS' });
    expect(validateFilename('wniosek$test.pdf')).toMatchObject({ code: 'FILENAME_FORBIDDEN_CHARS' });
    const longName = `${'a'.repeat(MAX_FILENAME_LENGTH)}.pdf`;
    expect(validateFilename(longName)).toMatchObject({ code: 'FILENAME_TOO_LONG' });
  });

  it('odrzuca pusty plik oraz plik przekraczający limit rozmiaru', () => {
    expect(validateFile(makeFile('pusty.pdf', 0)).error?.code).toBe('FILE_EMPTY');

    const overLimit = makeFile('duzy.pdf', (DEFAULT_MAX_FILE_SIZE_MB + 1) * 1024 * 1024);
    expect(validateFile(overLimit).error?.code).toBe('FILE_TOO_LARGE');
  });

  it('rozdziela poprawne i błędne pliki w paczce oraz pilnuje limitu plików', () => {
    const res = validateBatchFiles([
      makeFile('ok1.pdf', 100),
      makeFile('bad.zip', 100),
      makeFile('ok2.png', 200),
    ]);
    expect(res.validFiles).toHaveLength(2);
    expect(res.invalidFiles).toHaveLength(1);
    expect(res.batchError).toBeUndefined();

    const tooMany = Array.from({ length: DEFAULT_MAX_BATCH_FILES + 1 }, (_, i) =>
      makeFile(`plik_${i}.pdf`, 10),
    );
    const overBatch = validateBatchFiles(tooMany);
    expect(overBatch.batchError?.code).toBe('TOO_MANY_FILES');
  });
});
