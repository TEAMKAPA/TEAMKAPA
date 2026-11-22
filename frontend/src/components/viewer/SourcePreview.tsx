import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import {
  CHANNEL_LABELS,
  type FieldAssessment,
  type FieldSource,
  type SkippedElement,
} from '../../types/auditReport';
import { renderLocalFilePage, type RenderedPage } from '../../lib/pdfRenderer';
import { PageOverlay } from './PageOverlay';

interface SourcePreviewProps {
  filename: string;
  localFile: File | null;
  isDemoDocument: boolean;
  fields: FieldAssessment[];
  skippedElements: SkippedElement[];
  selectedFieldId: string | null;
  focusedSource: { source: FieldSource; label?: string } | null;
  currentPage: number;
  onPageChange: (page: number) => void;
  onSelectField: (fieldId: string) => void;
  onAttachLocalFile: (file: File) => void;
  fieldLabels: ReadonlyMap<string, string>;
}

function DemoBlankietSvg({ page }: { page: number }) {
  if (page === 1) {
    return (
      <svg
        viewBox="0 0 800 1131"
        className="block h-auto w-full select-none bg-white"
        role="img"
        aria-label="Podgląd strony 1 demonstracyjnego blankietu urzędowego"
      >
        <rect x="0" y="0" width="800" height="1131" fill="#ffffff" />
        <text x="56" y="64" fontSize="11" fill="#52555A" fontFamily="Inter, sans-serif">
          ZAŁĄCZNIK DO ROZPORZĄDZENIA MINISTRA ROZWOJU
        </text>
        <text x="56" y="94" fontSize="14" fill="#0D0E0E" fontFamily="Inter, sans-serif">
          WNIOSEK O USTALENIE NUMERU PORZĄDKOWEGO
        </text>
        <line x1="56" y1="108" x2="744" y2="108" stroke="#E5E7EB" strokeWidth="1" />

        <rect x="448" y="68" width="296" height="113" fill="#F3F3F4" stroke="#9FA1A5" strokeDasharray="5 3" />
        <text x="462" y="92" fontSize="11" fill="#52555A" fontFamily="Inter, sans-serif">
          ADNOTACJE URZĘDOWE (wypełnia urząd)
        </text>
        <text x="462" y="118" fontSize="10" fill="#9FA1A5" fontFamily="Inter, sans-serif">
          Data wpływu: ____-____-______
        </text>
        <text x="462" y="144" fontSize="10" fill="#9FA1A5" fontFamily="Inter, sans-serif">
          Numer sprawy: _____________________
        </text>

        <rect x="56" y="136" width="352" height="68" fill="#ffffff" stroke="#E5E7EB" />
        <text x="68" y="158" fontSize="12" fill="#52555A" fontFamily="Inter, sans-serif">
          Organ administracji (Urząd):
        </text>
        <text x="68" y="185" fontSize="14" fill="#0D0E0E" fontFamily="Inter, sans-serif">
          Urząd Miasta XXX, Wydział Geodezji
        </text>

        <text x="56" y="236" fontSize="14" fill="#0D0E0E" fontFamily="Inter, sans-serif">
          I. PODMIOT WNIOSKU
        </text>
        <rect x="56" y="249" width="688" height="90" fill="#ffffff" stroke="#E5E7EB" />
        <text x="68" y="276" fontSize="13" fill="#0D0E0E" fontFamily="Inter, sans-serif">
          Wniosek składam w imieniu:
        </text>
        <text x="68" y="310" fontSize="13" fill="#52555A" fontFamily="Inter, sans-serif">
          [ ] Swoim    [ ] Innej osoby    [ ] Osoby prawnej lub jednostki organizacyjnej
        </text>

        <rect x="56" y="351" width="688" height="56" fill="#ffffff" stroke="#E5E7EB" />
        <text x="68" y="384" fontSize="13" fill="#52555A" fontFamily="Inter, sans-serif">
          Działając jako:   [ ] Pełnomocnik      [ ] Reprezentant
        </text>

        <text x="56" y="446" fontSize="14" fill="#0D0E0E" fontFamily="Inter, sans-serif">
          II. DANE WNIOSKODAWCY
        </text>
        <rect x="56" y="464" width="328" height="68" fill="#ffffff" stroke="#E5E7EB" />
        <text x="68" y="488" fontSize="12" fill="#52555A" fontFamily="Inter, sans-serif">
          1. Imię wnioskodawcy
        </text>
        <line x1="66" y1="518" x2="372" y2="518" stroke="#9FA1A5" strokeDasharray="2 2" />

        <rect x="408" y="464" width="336" height="68" fill="#ffffff" stroke="#E5E7EB" />
        <text x="420" y="488" fontSize="12" fill="#52555A" fontFamily="Inter, sans-serif">
          2. Nazwisko wnioskodawcy
        </text>
        <line x1="418" y1="518" x2="732" y2="518" stroke="#9FA1A5" strokeDasharray="2 2" />

        <rect x="56" y="554" width="392" height="68" fill="#ffffff" stroke="#E5E7EB" />
        <text x="68" y="576" fontSize="12" fill="#52555A" fontFamily="Inter, sans-serif">
          3. Numer PESEL (11 cyfr)
        </text>
        {Array.from({ length: 11 }).map((_, i) => (
          <rect key={i} x={66 + i * 31} y="584" width="27" height="28" fill="#F3F3F4" stroke="#9FA1A5" />
        ))}

        <text x="56" y="668" fontSize="13" fill="#0D0E0E" fontFamily="Inter, sans-serif">
          III. DANE INNEJ OSOBY (MOCODAWCY)
        </text>
        <rect x="56" y="690" width="328" height="68" fill="#ffffff" stroke="#E5E7EB" />
        <text x="66" y="710" fontSize="10" fill="#52555A" fontFamily="Inter, sans-serif">
          Imię mocodawcy
        </text>
        <rect x="408" y="690" width="336" height="68" fill="#ffffff" stroke="#E5E7EB" />
        <text x="418" y="710" fontSize="10" fill="#52555A" fontFamily="Inter, sans-serif">
          Nazwisko mocodawcy
        </text>

        <rect x="56" y="780" width="392" height="68" fill="#ffffff" stroke="#E5E7EB" />
        <text x="66" y="800" fontSize="10" fill="#52555A" fontFamily="Inter, sans-serif">
          PESEL lub NIP mocodawcy
        </text>
        <line x1="66" y1="832" x2="430" y2="832" stroke="#9FA1A5" strokeDasharray="2 2" />

        <text x="56" y="886" fontSize="13" fill="#0D0E0E" fontFamily="Inter, sans-serif">
          IV. OPŁATA SKARBOWA ZA PEŁNOMOCNICTWO
        </text>
        <rect x="56" y="904" width="688" height="68" fill="#ffffff" stroke="#E5E7EB" />
        <text x="68" y="942" fontSize="12" fill="#0D0E0E" fontFamily="Inter, sans-serif">
          Czy złożenie pełnomocnictwa wymaga opłaty skarbowej (17 zł)?    [ ] Tak    [ ] Nie
        </text>

        <rect x="56" y="984" width="688" height="68" fill="#ffffff" stroke="#E5E7EB" />
        <text x="68" y="1022" fontSize="12" fill="#52555A" fontFamily="Inter, sans-serif">
          Załącznik: dowód uiszczenia opłaty skarbowej (potwierdzenie przelewu)
        </text>

        <text x="700" y="1104" fontSize="11" fill="#9FA1A5" fontFamily="Inter, sans-serif">
          Strona 1 / 2
        </text>
      </svg>
    );
  }

  return (
    <svg
      viewBox="0 0 800 1131"
      className="block h-auto w-full select-none bg-white"
      role="img"
      aria-label="Podgląd strony 2 demonstracyjnego blankietu urzędowego"
    >
      <rect x="0" y="0" width="800" height="1131" fill="#ffffff" />
      <text x="56" y="64" fontSize="12" fill="#52555A" fontFamily="Inter, sans-serif">
        WNIOSEK O USTALENIE NUMERU PORZĄDKOWEGO (CZĘŚĆ II)
      </text>
      <line x1="56" y1="78" x2="744" y2="78" stroke="#E5E7EB" strokeWidth="1" />

      <text x="56" y="116" fontSize="13" fill="#0D0E0E" fontFamily="Inter, sans-serif">
        V. LOKALIZACJA BUDYNKU / NIERUCHOMOŚCI
      </text>

      <rect x="56" y="136" width="328" height="68" fill="#ffffff" stroke="#E5E7EB" />
      <text x="66" y="156" fontSize="10" fill="#52555A" fontFamily="Inter, sans-serif">
        Kraj położenia nieruchomości
      </text>
      <text x="66" y="186" fontSize="12" fill="#0D0E0E" fontFamily="Inter, sans-serif">
        Polska
      </text>

      <rect x="408" y="136" width="336" height="68" fill="#ffffff" stroke="#E5E7EB" />
      <text x="418" y="156" fontSize="10" fill="#52555A" fontFamily="Inter, sans-serif">
        Miejscowość / dzielnica
      </text>

      <rect x="56" y="238" width="280" height="68" fill="#ffffff" stroke="#E5E7EB" />
      <text x="66" y="258" fontSize="10" fill="#52555A" fontFamily="Inter, sans-serif">
        Kod pocztowy
      </text>
      <text x="66" y="288" fontSize="13" fill="#9FA1A5" fontFamily="monospace">
        [__] - [___]
      </text>

      <rect x="368" y="238" width="376" height="68" fill="#ffffff" stroke="#E5E7EB" />
      <text x="378" y="258" fontSize="10" fill="#52555A" fontFamily="Inter, sans-serif">
        Numer działki ewidencyjnej i obręb
      </text>

      <rect x="56" y="339" width="360" height="68" fill="#ffffff" stroke="#E5E7EB" />
      <text x="66" y="359" fontSize="10" fill="#52555A" fontFamily="Inter, sans-serif">
        Data zakończenia budowy (opcjonalnie)
      </text>
      <text x="66" y="388" fontSize="12" fill="#9FA1A5" fontFamily="monospace">
        RRRR - MM - DD
      </text>

      <rect x="56" y="441" width="688" height="136" fill="#ffffff" stroke="#E5E7EB" />
      <text x="66" y="462" fontSize="10" fill="#52555A" fontFamily="Inter, sans-serif">
        Dodatkowe informacje / uzasadnienie wniosku
      </text>
      <line x1="66" y1="500" x2="730" y2="500" stroke="#E5E7EB" />
      <line x1="66" y1="536" x2="730" y2="536" stroke="#E5E7EB" />

      <text x="56" y="622" fontSize="13" fill="#0D0E0E" fontFamily="Inter, sans-serif">
        VI. DANE DO KONTAKTU (NIEOBOWIĄZKOWE)
      </text>
      <rect x="56" y="644" width="328" height="68" fill="#ffffff" stroke="#E5E7EB" />
      <text x="66" y="664" fontSize="10" fill="#52555A" fontFamily="Inter, sans-serif">
        Telefon kontaktowy
      </text>

      <rect x="408" y="644" width="336" height="68" fill="#ffffff" stroke="#E5E7EB" />
      <text x="418" y="664" fontSize="10" fill="#52555A" fontFamily="Inter, sans-serif">
        Adres poczty elektronicznej (e-mail)
      </text>

      <text x="56" y="750" fontSize="13" fill="#0D0E0E" fontFamily="Inter, sans-serif">
        VII. KLAUZULA RODO I ZGODA
      </text>
      <rect x="56" y="769" width="688" height="136" fill="#F3F3F4" stroke="#E5E7EB" />
      <text x="68" y="798" fontSize="12" fill="#0D0E0E" fontFamily="Inter, sans-serif">
        [x] Wyrażam zgodę na przetwarzanie moich danych osobowych zgodnie z klauzulą
      </text>
      <text x="86" y="820" fontSize="12" fill="#0D0E0E" fontFamily="Inter, sans-serif">
        informacyjną w celu rozpatrzenia wniosku o ustalenie numeru porządkowego.
      </text>

      <rect x="432" y="950" width="304" height="102" fill="#F3F3F4" stroke="#9FA1A5" strokeDasharray="5 3" />
      <text x="448" y="1028" fontSize="11" fill="#52555A" fontFamily="Inter, sans-serif">
        Data i czytelny podpis wnioskodawcy
      </text>

      <text x="700" y="1104" fontSize="11" fill="#9FA1A5" fontFamily="Inter, sans-serif">
        Strona 2 / 2
      </text>
    </svg>
  );
}

/**
 * Lewy panel ekranu weryfikacji:
 * Biały kontener #FFFFFF, 1px solid #E5E7EB, rounded-lg, shadow-none.
 * Prosty nagłówek „Podgląd dokumentu” oraz nawigacja stron „Poprzednia” / „Następna”.
 * Zero ikon — pozostają wyłącznie prostokąty zaznaczeń (bbox) na podglądzie dokumentu.
 */
export function SourcePreview({
  filename,
  localFile,
  isDemoDocument,
  fields,
  skippedElements,
  selectedFieldId,
  focusedSource,
  currentPage,
  onPageChange,
  onSelectField,
  onAttachLocalFile,
  fieldLabels,
}: SourcePreviewProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [renderedPage, setRenderedPage] = useState<RenderedPage | null>(null);
  const [isRendering, setIsRendering] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);

  const maxReportPage = Math.max(
    1,
    ...fields.flatMap((f) => f.sources.map((s) => s.page ?? 1)),
    ...skippedElements.map((s) => s.source.page ?? 1),
    isDemoDocument ? 2 : 1,
  );
  const totalPages = Math.max(maxReportPage, renderedPage?.totalPages ?? 1);

  useEffect(() => {
    if (!localFile) {
      setRenderedPage(null);
      setRenderError(null);
      return;
    }

    let cancelled = false;
    setIsRendering(true);
    setRenderError(null);

    renderLocalFilePage(localFile, currentPage)
      .then((res) => {
        if (!cancelled) {
          setRenderedPage(res);
          setIsRendering(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRenderedPage(null);
          setRenderError(
            'Ten format pliku nie ma podglądu graficznego w przeglądarce. Wyświetlono mapę zaznaczeń.',
          );
          setIsRendering(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [localFile, currentPage]);

  const handleAttachChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      onAttachLocalFile(file);
      e.target.value = '';
    }
  };

  const selectedAssessment = fields.find((f) => f.field_id === selectedFieldId) ?? null;
  const activeSource: FieldSource | null =
    focusedSource?.source ?? selectedAssessment?.sources[0] ?? null;

  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] shadow-none">
      {/* Górny pasek panelu: nagłówek i nawigacja stron bez ikon */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#E5E7EB] px-4 py-4 sm:px-6">
        <div className="min-w-0">
          <h2 className="text-[20px] font-normal leading-7 text-[#0D0E0E] sm:text-[22px]">
            Podgląd dokumentu
          </h2>
          <p className="mt-0.5 truncate text-sm font-normal text-[#52555A]" title={filename}>
            {filename}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => onPageChange(Math.max(1, currentPage - 1))}
            disabled={currentPage <= 1}
            className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-2.5 py-1.5 text-sm font-normal text-[#0D0E0E] hover:bg-[#F3F3F4] disabled:opacity-40 sm:px-3"
          >
            Poprzednia
          </button>
          <span className="px-1 text-sm font-normal text-[#52555A] tabular-nums sm:px-2">
            Strona {currentPage} z {totalPages}
          </span>
          <button
            type="button"
            onClick={() => onPageChange(Math.min(totalPages, currentPage + 1))}
            disabled={currentPage >= totalPages}
            className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-2.5 py-1.5 text-sm font-normal text-[#0D0E0E] hover:bg-[#F3F3F4] disabled:opacity-40 sm:px-3"
          >
            Następna
          </button>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-2.5 py-1.5 text-sm font-normal text-[#52555A] hover:bg-[#F3F3F4] hover:text-[#0D0E0E] sm:px-3"
          >
            Podmień plik
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
            onChange={handleAttachChange}
            className="hidden"
          />
        </div>
      </div>

      {/* Obszar strony z nakładką bbox — bezpośrednio w białym panelu bez boksa w boksie */}
      <div className="thin-scrollbar max-h-[52vh] overflow-auto bg-[#FFFFFF] lg:max-h-[68vh]">
        {isRendering ? (
          <div className="flex h-72 items-center justify-center text-sm font-normal text-[#52555A]">
            Ładowanie strony {currentPage}...
          </div>
        ) : (
          <div className="relative w-full bg-[#FFFFFF]">
            {renderedPage ? (
              <img
                src={renderedPage.dataUrl}
                alt={`Strona ${currentPage} dokumentu ${filename}`}
                className="block h-auto w-full select-none"
              />
            ) : isDemoDocument ? (
              <DemoBlankietSvg page={currentPage} />
            ) : (
              <div className="relative flex aspect-[1/1.414] w-full flex-col bg-white p-8">
                <div className="border-b border-[#E5E7EB] pb-4">
                  <div className="text-base font-normal text-[#0D0E0E]">
                    Podgląd współrzędnych (strona {currentPage})
                  </div>
                  <p className="mt-1 text-sm font-normal text-[#52555A]">
                    {renderError ??
                      'Serwer nie przechowuje oryginalnego pliku. Użyj przycisku „Podmień plik”, aby wyświetlić lokalny plik PDF lub obraz.'}
                  </p>
                </div>
              </div>
            )}

            <PageOverlay
              fields={fields}
              skippedSources={skippedElements}
              currentPage={currentPage}
              selectedFieldId={selectedFieldId}
              focusedSource={focusedSource}
              showAllBoxes
              fieldLabels={fieldLabels}
              onSelectField={onSelectField}
            />
          </div>
        )}
      </div>

      {/* Stopka z informacją o wybranym źródle */}
      <div className="border-t border-[#E5E7EB] bg-[#FFFFFF] px-6 py-3 text-sm font-normal text-[#52555A]">
        {activeSource ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="truncate text-[#0D0E0E]">
              {activeSource.text ? `Źródło: „${activeSource.text}”` : 'Wskazano obszar na stronie'}
            </span>
            <span className="shrink-0 text-xs text-[#52555A]">
              {CHANNEL_LABELS[activeSource.channel] ?? activeSource.channel}
              {activeSource.page ? ` · Strona ${activeSource.page}` : ''}
            </span>
          </div>
        ) : (
          <span>Kliknij pole po prawej lub prostokąt na dokumencie, aby wskazać źródło.</span>
        )}
      </div>
    </div>
  );
}
