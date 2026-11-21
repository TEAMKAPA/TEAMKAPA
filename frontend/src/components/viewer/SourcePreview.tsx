import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  FileUp,
  Loader2,
  MapPin,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import {
  CHANNEL_LABELS,
  type FieldAssessment,
  type FieldSource,
  type SkippedElement,
} from '../../types/auditReport';
import { renderLocalFilePage, type RenderedPage } from '../../lib/pdfRenderer';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
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
        <text x="56" y="64" fontSize="12" fill="#475569" fontFamily="sans-serif">
          ZAŁĄCZNIK DO ROZPORZĄDZENIA MINISTRA ROZWOJU
        </text>
        <text x="56" y="96" fontSize="18" fontWeight="700" fill="#0f172a" fontFamily="sans-serif">
          WNIOSEK O USTALENIE NUMERU PORZĄDKOWEGO
        </text>
        <line x1="56" y1="108" x2="744" y2="108" stroke="#cbd5e1" strokeWidth="1.5" />

        <rect
          x="448"
          y="68"
          width="296"
          height="113"
          fill="#f8fafc"
          stroke="#94a3b8"
          strokeDasharray="5 3"
        />
        <text x="462" y="92" fontSize="11" fontWeight="600" fill="#64748b" fontFamily="sans-serif">
          ADNOTACJE URZĘDOWE (wypełnia urząd)
        </text>
        <text x="462" y="118" fontSize="10" fill="#94a3b8" fontFamily="sans-serif">
          Data wpływu: ____-____-______
        </text>
        <text x="462" y="144" fontSize="10" fill="#94a3b8" fontFamily="sans-serif">
          Numer sprawy: _____________________
        </text>

        <rect x="56" y="136" width="352" height="68" fill="#ffffff" stroke="#cbd5e1" />
        <text x="66" y="156" fontSize="10" fontWeight="600" fill="#475569" fontFamily="sans-serif">
          Organ administracji (Urząd):
        </text>
        <text x="66" y="182" fontSize="13" fill="#0f172a" fontFamily="sans-serif">
          Urząd Miasta XXX, Wydział Geodezji
        </text>

        <text x="56" y="236" fontSize="13" fontWeight="700" fill="#1e293b" fontFamily="sans-serif">
          I. PODMIOT WNIOSKU
        </text>
        <rect x="56" y="249" width="688" height="90" fill="#ffffff" stroke="#cbd5e1" />
        <text x="68" y="274" fontSize="12" fill="#0f172a" fontFamily="sans-serif">
          Wniosek składam w imieniu:
        </text>
        <text x="68" y="308" fontSize="12" fill="#334155" fontFamily="sans-serif">
          ☐ Swoim      ☐ Innej osoby      ☐ Osoby prawnej lub jednostki organizacyjnej
        </text>

        <rect x="56" y="351" width="688" height="56" fill="#ffffff" stroke="#cbd5e1" />
        <text x="68" y="384" fontSize="12" fill="#334155" fontFamily="sans-serif">
          Działając jako (dla podmiotu):   ☐ Pełnomocnik      ☐ Reprezentant
        </text>

        <text x="56" y="446" fontSize="13" fontWeight="700" fill="#1e293b" fontFamily="sans-serif">
          II. DANE WNIOSKODAWCY
        </text>
        <rect x="56" y="464" width="328" height="68" fill="#ffffff" stroke="#cbd5e1" />
        <text x="66" y="484" fontSize="10" fill="#475569" fontFamily="sans-serif">
          1. Imię wnioskodawcy
        </text>
        <line x1="66" y1="518" x2="372" y2="518" stroke="#94a3b8" strokeDasharray="2 2" />

        <rect x="408" y="464" width="336" height="68" fill="#ffffff" stroke="#cbd5e1" />
        <text x="418" y="484" fontSize="10" fill="#475569" fontFamily="sans-serif">
          2. Nazwisko wnioskodawcy
        </text>
        <line x1="418" y1="518" x2="732" y2="518" stroke="#94a3b8" strokeDasharray="2 2" />

        <rect x="56" y="554" width="392" height="68" fill="#ffffff" stroke="#cbd5e1" />
        <text x="66" y="574" fontSize="10" fill="#475569" fontFamily="sans-serif">
          3. Numer PESEL (11 cyfr)
        </text>
        {Array.from({ length: 11 }).map((_, i) => (
          <rect
            key={i}
            x={66 + i * 31}
            y="584"
            width="27"
            height="28"
            fill="#f8fafc"
            stroke="#64748b"
          />
        ))}

        <text x="56" y="668" fontSize="13" fontWeight="700" fill="#1e293b" fontFamily="sans-serif">
          III. DANE INNEJ OSOBY (MOCODAWCY)
        </text>
        <rect x="56" y="690" width="328" height="68" fill="#ffffff" stroke="#cbd5e1" />
        <text x="66" y="710" fontSize="10" fill="#475569" fontFamily="sans-serif">
          Imię mocodawcy
        </text>
        <rect x="408" y="690" width="336" height="68" fill="#ffffff" stroke="#cbd5e1" />
        <text x="418" y="710" fontSize="10" fill="#475569" fontFamily="sans-serif">
          Nazwisko mocodawcy
        </text>

        <rect x="56" y="780" width="392" height="68" fill="#ffffff" stroke="#cbd5e1" />
        <text x="66" y="800" fontSize="10" fill="#475569" fontFamily="sans-serif">
          PESEL lub NIP mocodawcy (skan lekko rozmyty)
        </text>
        <line x1="66" y1="832" x2="430" y2="832" stroke="#94a3b8" strokeDasharray="2 2" />

        <text x="56" y="886" fontSize="13" fontWeight="700" fill="#1e293b" fontFamily="sans-serif">
          IV. OPŁATA SKARBOWA ZA PEŁNOMOCNICTWO
        </text>
        <rect x="56" y="904" width="688" height="68" fill="#ffffff" stroke="#cbd5e1" />
        <text x="68" y="942" fontSize="12" fill="#0f172a" fontFamily="sans-serif">
          Czy złożenie pełnomocnictwa wymaga opłaty skarbowej (17 zł)?    ☐ Tak    ☐ Nie
        </text>

        <rect x="56" y="984" width="688" height="68" fill="#ffffff" stroke="#cbd5e1" />
        <text x="68" y="1022" fontSize="12" fill="#475569" fontFamily="sans-serif">
          Załącznik: dowód uiszczenia opłaty skarbowej (potwierdzenie przelewu)
        </text>

        <text x="700" y="1104" fontSize="11" fill="#94a3b8" fontFamily="sans-serif">
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
      <text x="56" y="64" fontSize="12" fill="#475569" fontFamily="sans-serif">
        WNIOSEK O USTALENIE NUMERU PORZĄDKOWEGO — CZĘŚĆ II
      </text>
      <line x1="56" y1="78" x2="744" y2="78" stroke="#cbd5e1" strokeWidth="1.5" />

      <text x="56" y="116" fontSize="13" fontWeight="700" fill="#1e293b" fontFamily="sans-serif">
        V. LOKALIZACJA BUDYNKU / NIERUCHOMOŚCI
      </text>

      <rect x="56" y="136" width="328" height="68" fill="#ffffff" stroke="#cbd5e1" />
      <text x="66" y="156" fontSize="10" fill="#475569" fontFamily="sans-serif">
        Kraj położenia nieruchomości
      </text>
      <text x="66" y="186" fontSize="12" fill="#0f172a" fontFamily="sans-serif">
        Polska
      </text>

      <rect x="408" y="136" width="336" height="68" fill="#ffffff" stroke="#cbd5e1" />
      <text x="418" y="156" fontSize="10" fill="#475569" fontFamily="sans-serif">
        Miejscowość / dzielnica
      </text>

      <rect x="56" y="238" width="280" height="68" fill="#ffffff" stroke="#cbd5e1" />
      <text x="66" y="258" fontSize="10" fill="#475569" fontFamily="sans-serif">
        Kod pocztowy
      </text>
      <text x="66" y="288" fontSize="13" fill="#64748b" fontFamily="monospace">
        [__] - [___]
      </text>

      <rect x="368" y="238" width="376" height="68" fill="#ffffff" stroke="#cbd5e1" />
      <text x="378" y="258" fontSize="10" fill="#475569" fontFamily="sans-serif">
        Numer działki ewidencyjnej i obręb
      </text>

      <rect x="56" y="339" width="360" height="68" fill="#ffffff" stroke="#cbd5e1" />
      <text x="66" y="359" fontSize="10" fill="#475569" fontFamily="sans-serif">
        Data zakończenia budowy (opcjonalnie)
      </text>
      <text x="66" y="388" fontSize="12" fill="#94a3b8" fontFamily="monospace">
        RRRR - MM - DD
      </text>

      <rect x="56" y="441" width="688" height="136" fill="#ffffff" stroke="#cbd5e1" />
      <text x="66" y="462" fontSize="10" fill="#475569" fontFamily="sans-serif">
        Dodatkowe informacje / uzasadnienie wniosku
      </text>
      <line x1="66" y1="500" x2="730" y2="500" stroke="#e2e8f0" />
      <line x1="66" y1="536" x2="730" y2="536" stroke="#e2e8f0" />

      <text x="56" y="622" fontSize="13" fontWeight="700" fill="#1e293b" fontFamily="sans-serif">
        VI. DANE DO KONTAKTU (NIEOBOWIĄZKOWE)
      </text>
      <rect x="56" y="644" width="328" height="68" fill="#ffffff" stroke="#cbd5e1" />
      <text x="66" y="664" fontSize="10" fill="#475569" fontFamily="sans-serif">
        Telefon kontaktowy
      </text>

      <rect x="408" y="644" width="336" height="68" fill="#ffffff" stroke="#cbd5e1" />
      <text x="418" y="664" fontSize="10" fill="#475569" fontFamily="sans-serif">
        Adres poczty elektronicznej (e-mail)
      </text>

      <text x="56" y="750" fontSize="13" fontWeight="700" fill="#1e293b" fontFamily="sans-serif">
        VII. KLAUZULA RODO I ZGODA
      </text>
      <rect x="56" y="769" width="688" height="136" fill="#f8fafc" stroke="#cbd5e1" />
      <text x="68" y="798" fontSize="12" fill="#0f172a" fontFamily="sans-serif">
        ☑ Wyrażam zgodę na przetwarzanie moich danych osobowych zgodnie z klauzulą
      </text>
      <text x="86" y="820" fontSize="12" fill="#0f172a" fontFamily="sans-serif">
        informacyjną w celu rozpatrzenia wniosku o ustalenie numeru porządkowego.
      </text>

      <rect
        x="432"
        y="950"
        width="304"
        height="102"
        fill="#f8fafc"
        stroke="#94a3b8"
        strokeDasharray="5 3"
      />
      <text x="448" y="1028" fontSize="11" fill="#64748b" fontFamily="sans-serif">
        Data i czytelny podpis wnioskodawcy
      </text>

      <text x="700" y="1104" fontSize="11" fill="#94a3b8" fontFamily="sans-serif">
        Strona 2 / 2
      </text>
    </svg>
  );
}

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
  const [zoom, setZoom] = useState(100);
  const [showAllBoxes, setShowAllBoxes] = useState(true);

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
            'Ten format pliku nie ma podglądu graficznego w przeglądarce — wyświetlono mapę współrzędnych bbox.',
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
    <div className="flex h-full flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xs">
      {/* Czysty pasek sterowania podglądem dokumentu */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex items-center gap-2">
          <div className="inline-flex items-center rounded-lg border border-slate-200 bg-slate-50">
            <button
              type="button"
              onClick={() => onPageChange(Math.max(1, currentPage - 1))}
              disabled={currentPage <= 1}
              aria-label="Poprzednia strona"
              className="p-1.5 text-slate-700 hover:bg-slate-200/70 disabled:opacity-35"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="px-2.5 text-xs font-semibold text-slate-800 tabular-nums">
              Strona {currentPage} z {totalPages}
            </span>
            <button
              type="button"
              onClick={() => onPageChange(Math.min(totalPages, currentPage + 1))}
              disabled={currentPage >= totalPages}
              aria-label="Następna strona"
              className="p-1.5 text-slate-700 hover:bg-slate-200/70 disabled:opacity-35"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>

          <div className="inline-flex items-center rounded-lg border border-slate-200 bg-slate-50">
            <button
              type="button"
              onClick={() => setZoom((z) => Math.max(75, z - 15))}
              aria-label="Pomniejsz"
              className="p-1.5 text-slate-700 hover:bg-slate-200/70"
            >
              <ZoomOut className="h-4 w-4" />
            </button>
            <span className="px-2 text-xs font-medium text-slate-700 tabular-nums">{zoom}%</span>
            <button
              type="button"
              onClick={() => setZoom((z) => Math.min(165, z + 15))}
              aria-label="Powiększ"
              className="p-1.5 text-slate-700 hover:bg-slate-200/70"
            >
              <ZoomIn className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          <Button
            variant="ghost"
            size="xs"
            onClick={() => setShowAllBoxes((v) => !v)}
            title={showAllBoxes ? 'Pokaż tylko zaznaczone pole' : 'Pokaż wszystkie ramki na stronie'}
          >
            {showAllBoxes ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
            <span>{showAllBoxes ? 'Ramki: wszystkie' : 'Ramki: wybrane'}</span>
          </Button>

          <Button
            variant="ghost"
            size="xs"
            onClick={() => fileInputRef.current?.click()}
            title="Podłącz lokalny plik PDF/PNG/JPG do podglądu strony"
          >
            <FileUp className="h-4 w-4" />
            <span className="hidden xl:inline">Podmień skan</span>
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
            onChange={handleAttachChange}
            className="hidden"
          />
        </div>
      </div>

      {/* Czytelna legenda kolorów pewności */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 bg-slate-50/80 px-4 py-2 text-xs text-slate-600">
        <div className="flex items-center gap-4">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
            Pewne
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-amber-500" />
            Do przeglądu
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-rose-500" />
            Niska pewność
          </span>
        </div>
        <span className="truncate text-slate-500" title={filename}>
          {filename}
        </span>
      </div>

      {/* Obszar dokumentu */}
      <div className="thin-scrollbar relative flex-1 overflow-auto bg-slate-100 p-5">
        {isRendering && (
          <div className="flex h-72 flex-col items-center justify-center gap-3 text-sm text-slate-600">
            <Loader2 className="h-7 w-7 animate-spin text-blue-700" />
            <span>Renderowanie strony {currentPage}…</span>
          </div>
        )}

        {!isRendering && (
          <div className="mx-auto transition-all" style={{ width: `${zoom}%`, maxWidth: '960px' }}>
            <div className="relative overflow-hidden rounded-lg bg-white shadow-md ring-1 ring-slate-300/80">
              {renderedPage ? (
                <img
                  src={renderedPage.dataUrl}
                  alt={`Strona ${currentPage} dokumentu ${filename}`}
                  className="block h-auto w-full select-none"
                />
              ) : isDemoDocument ? (
                <DemoBlankietSvg page={currentPage} />
              ) : (
                <div className="relative flex aspect-[1/1.414] w-full flex-col justify-between bg-white p-8">
                  <div className="space-y-2 border-b border-slate-200 pb-4">
                    <div className="text-base font-semibold text-slate-800">
                      Podgląd współrzędnych — Strona {currentPage}
                    </div>
                    <p className="text-sm text-slate-500">
                      {renderError ??
                        'Dla ochrony prywatności serwer nie przechowuje oryginalnego pliku. Użyj przycisku „Podmień skan”, aby podglądać lokalny PDF lub obraz pod ramkami.'}
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
                showAllBoxes={showAllBoxes}
                fieldLabels={fieldLabels}
                onSelectField={onSelectField}
              />
            </div>
          </div>
        )}
      </div>

      {/* Stopka ze źródłem zaznaczonego pola */}
      <div className="border-t border-slate-200 bg-white px-4 py-3 text-xs">
        {activeSource ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <MapPin className="h-4 w-4 shrink-0 text-blue-600" />
              <span className="truncate text-sm font-medium text-slate-900">
                {activeSource.text ? `„${activeSource.text}”` : 'Wskazano obszar na dokumencie'}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2 text-slate-500">
              <Badge variant="neutral">
                {CHANNEL_LABELS[activeSource.channel] ?? activeSource.channel}
              </Badge>
              {activeSource.page && <span className="font-medium">Str. {activeSource.page}</span>}
            </div>
          </div>
        ) : (
          <div className="text-sm text-slate-500">
            Kliknij pole na liście po prawej lub ramkę na blankiecie, aby podświetlić jego źródło.
          </div>
        )}
      </div>
    </div>
  );
}
