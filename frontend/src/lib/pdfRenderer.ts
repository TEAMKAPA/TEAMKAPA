/**
 * Renderowanie stron lokalnego pliku (PDF / PNG / JPG) po stronie przeglądarki.
 *
 * Backend COI celowo nie utrwala przesłanych plików — renderowanie w przeglądarce
 * pozwala wyświetlać dokładny podgląd strony wraz z nakładką bbox bez przechowywania
 * dokumentów na serwerze.
 */

import { extractExtension, isImageExtension, isPdfExtension } from './fileValidation';

export interface RenderedPage {
  pageNumber: number;
  totalPages: number;
  dataUrl: string;
  width: number;
  height: number;
}

const pageCache = new WeakMap<File, Map<number, RenderedPage>>();

let pdfWorkerConfigured = false;

async function loadPdfJs() {
  const pdfjs = await import('pdfjs-dist');
  if (!pdfWorkerConfigured) {
    pdfjs.GlobalWorkerOptions.workerSrc = new URL(
      'pdfjs-dist/build/pdf.worker.min.mjs',
      import.meta.url,
    ).href;
    pdfWorkerConfigured = true;
  }
  return pdfjs;
}

export async function renderLocalFilePage(
  file: File,
  pageNumber: number,
  scale = 1.6,
): Promise<RenderedPage | null> {
  const parsed = extractExtension(file.name);
  const ext = parsed?.extension ?? null;

  let cacheForFile = pageCache.get(file);
  if (!cacheForFile) {
    cacheForFile = new Map<number, RenderedPage>();
    pageCache.set(file, cacheForFile);
  }

  const cached = cacheForFile.get(pageNumber);
  if (cached) {
    return cached;
  }

  if (isImageExtension(ext) || file.type.startsWith('image/')) {
    const dataUrl = await fileToDataUrl(file);
    const dims = await readImageDimensions(dataUrl);
    const rendered: RenderedPage = {
      pageNumber: 1,
      totalPages: 1,
      dataUrl,
      width: dims.width,
      height: dims.height,
    };
    cacheForFile.set(1, rendered);
    return rendered;
  }

  if (isPdfExtension(ext) || file.type === 'application/pdf') {
    const pdfjs = await loadPdfJs();
    const buffer = await file.arrayBuffer();
    const loadingTask = pdfjs.getDocument({ data: new Uint8Array(buffer) });
    const pdfDoc = await loadingTask.promise;
    const totalPages = pdfDoc.numPages;
    const clampedPage = Math.min(Math.max(1, pageNumber), totalPages);
    const page = await pdfDoc.getPage(clampedPage);
    const viewport = page.getViewport({ scale });

    const canvas = document.createElement('canvas');
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      await pdfDoc.destroy();
      return null;
    }

    await page.render({ canvasContext: ctx, viewport }).promise;
    const dataUrl = canvas.toDataURL('image/png');
    await pdfDoc.destroy();

    const rendered: RenderedPage = {
      pageNumber: clampedPage,
      totalPages,
      dataUrl,
      width: canvas.width,
      height: canvas.height,
    };
    cacheForFile.set(clampedPage, rendered);
    return rendered;
  }

  return null;
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error('Nie udało się odczytać obrazu'));
    reader.readAsDataURL(file);
  });
}

function readImageDimensions(dataUrl: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve({ width: img.naturalWidth || 800, height: img.naturalHeight || 1131 });
    img.onerror = () => resolve({ width: 800, height: 1131 });
    img.src = dataUrl;
  });
}
