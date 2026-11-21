import {
  AlertCircle,
  AlertTriangle,
  Check,
  ChevronRight,
  Lock,
  MapPin,
  Upload,
} from 'lucide-react';
import {
  FIELD_TYPE_LABELS,
  VALIDATOR_LABELS,
  getFieldTitle,
  isChoiceField,
  type LeafField,
  type Validator,
} from '../../types/coiForm';
import type { FieldAssessment, Issue } from '../../types/auditReport';
import { describeStateQuery, type AnswerValue } from '../../lib/conditionEvaluator';
import { Badge } from '../ui/Badge';
import { CONFIDENCE_STYLES, ConfidenceBadge } from '../audit/ConfidenceBadge';

interface FieldFactoryProps {
  field: LeafField;
  mode: 'review' | 'interactive';
  assessment?: FieldAssessment;
  liveIssues?: Issue[];
  isSelected?: boolean;
  isReviewed?: boolean;
  isModified?: boolean;
  onSelect?: (fieldId: string) => void;
  onToggleReviewed?: (fieldId: string) => void;
  value?: AnswerValue;
  onChangeValue?: (val: AnswerValue) => void;
  dynamicReadOnly?: boolean;
  validationError?: string;
}

const SAMPLE_DICT_VALUES: Record<string, string[]> = {
  Kraje: ['Polska', 'Niemcy', 'Czechy', 'Słowacja', 'Ukraina', 'Litwa'],
  Województwo: [
    'mazowieckie',
    'małopolskie',
    'dolnośląskie',
    'pomorskie',
    'śląskie',
    'wielkopolskie',
  ],
  Powiat: ['m. st. Warszawa', 'krakowski', 'wrocławski', 'gdański', 'poznański'],
  Gmina: ['Warszawa', 'Kraków', 'Wrocław', 'Gdańsk', 'Poznań'],
  Miejscowość: ['Warszawa', 'Kraków', 'Wrocław', 'Gdańsk', 'Poznań'],
  'Numery kierunkowe': ['+48 (Polska)', '+49 (Niemcy)', '+420 (Czechy)', '+44 (Wielka Brytania)'],
};

export function FieldFactory({
  field,
  mode,
  assessment,
  liveIssues = [],
  isSelected = false,
  isReviewed = false,
  isModified = false,
  onSelect,
  onToggleReviewed,
  value,
  onChangeValue,
  dynamicReadOnly = false,
  validationError,
}: FieldFactoryProps) {
  const title = getFieldTitle(field);
  const readOnly = ('readOnly' in field && Boolean(field.readOnly)) || dynamicReadOnly;
  const validators: readonly Validator[] = 'validation' in field ? field.validation : [];
  const isRequired = validators.includes('REQUIRED');

  // ---------------------------------------------------------------- Tryb interaktywnego wypełniania
  if (mode === 'interactive') {
    const inputClass =
      'w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-blue-600 focus:ring-2 focus:ring-blue-600/15 focus:outline-none disabled:bg-slate-100 disabled:text-slate-500';

    return (
      <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-4">
        {field.type !== 'INFO_SECTION' && field.type !== 'CONSENT_SECTION' && (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className="text-sm font-semibold text-slate-900">
              {title}
              {isRequired && <span className="ml-1 text-rose-600">*</span>}
            </label>
            <div className="flex items-center gap-1.5">
              {readOnly && (
                <Badge variant="neutral">
                  <Lock className="h-3 w-3" />
                  Tylko do odczytu
                </Badge>
              )}
              {'defaultValueType' in field && field.defaultValueType === 'EXTERNAL_SYSTEM' && (
                <Badge variant="info">{field.defaultValue}</Badge>
              )}
            </div>
          </div>
        )}

        {'description' in field &&
          field.description &&
          field.type !== 'INFO_SECTION' &&
          field.type !== 'TEXT_BLOCK' && (
            <p className="text-xs text-slate-500">{field.description}</p>
          )}

        {field.type === 'TEXT_FIELD' && (
          <input
            type="text"
            value={String(value ?? field.defaultValue ?? '')}
            disabled={readOnly}
            onChange={(e) => onChangeValue?.(e.target.value)}
            placeholder={field.tooltipMessage ?? `Wpisz: ${title}`}
            className={inputClass}
          />
        )}

        {field.type === 'NUMBER' && (
          <input
            type="number"
            value={String(value ?? '')}
            disabled={readOnly}
            onChange={(e) => onChangeValue?.(e.target.value)}
            className={inputClass}
          />
        )}

        {field.type === 'TEXTAREA' && (
          <textarea
            rows={3}
            value={String(value ?? field.defaultValue ?? '')}
            disabled={readOnly}
            onChange={(e) => onChangeValue?.(e.target.value)}
            className={inputClass}
          />
        )}

        {field.type === 'DATEPICKER' && (
          <input
            type="date"
            value={String(value ?? '')}
            disabled={readOnly}
            onChange={(e) => onChangeValue?.(e.target.value)}
            className={inputClass}
          />
        )}

        {field.type === 'SELECT' && (
          <select
            value={String(value ?? field.defaultValue ?? '')}
            disabled={readOnly}
            onChange={(e) => onChangeValue?.(e.target.value)}
            className={inputClass}
          >
            <option value="">— Wybierz z listy —</option>
            {field.dictionaryName
              ? (SAMPLE_DICT_VALUES[field.dictionaryName] ?? [field.dictionaryName]).map((opt) => (
                  <option key={opt} value={opt}>
                    {opt}
                  </option>
                ))
              : field.options.map((opt) => (
                  <option key={opt.id} value={opt.name}>
                    {opt.name}
                  </option>
                ))}
          </select>
        )}

        {field.type === 'RADIO' && (
          <div className="space-y-2 pt-1">
            {field.options.map((opt) => {
              const checked = String(value ?? '') === opt.name;
              return (
                <label
                  key={opt.id}
                  className={`flex cursor-pointer items-center gap-3 rounded-xl border px-3.5 py-2.5 text-sm transition-colors ${
                    checked
                      ? 'border-blue-600 bg-blue-50/70 font-medium text-blue-950'
                      : 'border-slate-200 text-slate-800 hover:bg-slate-50'
                  }`}
                >
                  <input
                    type="radio"
                    name={`preview-${field.id}`}
                    value={opt.name}
                    checked={checked}
                    disabled={readOnly}
                    onChange={() => onChangeValue?.(opt.name)}
                    className="h-4 w-4 text-blue-700"
                  />
                  <span>{opt.name}</span>
                </label>
              );
            })}
          </div>
        )}

        {field.type === 'CHECKBOX' && (
          <div className="space-y-2 pt-1">
            {field.options.map((opt) => {
              const currentList = Array.isArray(value) ? value : [];
              const checked = currentList.includes(opt.name);
              return (
                <label
                  key={opt.id}
                  className="flex cursor-pointer items-center gap-3 rounded-xl border border-slate-200 px-3.5 py-2.5 text-sm text-slate-800 hover:bg-slate-50"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={readOnly}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...currentList, opt.name]
                        : currentList.filter((v) => v !== opt.name);
                      onChangeValue?.(next);
                    }}
                    className="h-4 w-4"
                  />
                  <span>{opt.name}</span>
                </label>
              );
            })}
          </div>
        )}

        {field.type === 'FILE_UPLOADER' && (
          <label className="flex cursor-pointer items-center justify-center gap-2.5 rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-4 text-sm text-slate-600 hover:border-blue-500">
            <Upload className="h-5 w-5 text-slate-400" />
            <span>{value ? String(value) : 'Wybierz przykładowy plik załącznika…'}</span>
            <input
              type="file"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                onChangeValue?.(f ? f.name : '');
              }}
            />
          </label>
        )}

        {field.type === 'TEXT_BLOCK' && (
          <div className="rounded-xl bg-slate-50 p-3.5 text-sm leading-relaxed text-slate-700">
            {field.description ?? field.title}
          </div>
        )}

        {field.type === 'INFO_SECTION' && (
          <div className="rounded-xl border border-blue-200 bg-blue-50/70 p-4 text-sm leading-relaxed text-blue-950">
            {field.description}
          </div>
        )}

        {field.type === 'CONSENT_SECTION' && (
          <div className="space-y-2.5">
            {field.consentSectionConfig.consents.map((consent, idx) => {
              const checkedList = Array.isArray(value) ? value : [];
              const key = String(idx);
              const checked = checkedList.includes(key);
              return (
                <label
                  key={idx}
                  className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-200 bg-slate-50/60 p-3.5 text-sm leading-relaxed text-slate-800"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...checkedList, key]
                        : checkedList.filter((k) => k !== key);
                      onChangeValue?.(next);
                    }}
                    className="mt-1 h-4 w-4"
                  />
                  <span>
                    {consent.description}
                    {consent.required && (
                      <span className="ml-1 font-semibold text-rose-600">*</span>
                    )}
                  </span>
                </label>
              );
            })}
          </div>
        )}

        {validationError && (
          <p className="flex items-center gap-1.5 text-xs font-medium text-rose-600">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {validationError}
          </p>
        )}
      </div>
    );
  }

  // ---------------------------------------------------------------- Tryb przeglądu urzędnika (czysty, bez szumu)
  const level = assessment?.level ?? 'HIGH';
  const style = CONFIDENCE_STYLES[level];
  const allIssues = [...(assessment?.issues ?? []), ...liveIssues];
  const hasErrors = allIssues.some((i) => i.severity === 'ERROR');
  const firstSource = assessment?.sources[0];

  // Jedna spokojna linia streszczenia zamiast „zupy z pigułek”
  const metaParts: string[] = [FIELD_TYPE_LABELS[field.type]];
  const domainValidators = validators.filter((v) => v !== 'REQUIRED');
  if (domainValidators.length > 0) {
    metaParts.push(domainValidators.map((v) => VALIDATOR_LABELS[v]).join(', '));
  }
  if (isChoiceField(field)) {
    if (field.dictionaryName) {
      metaParts.push(`Słownik: ${field.dictionaryName}`);
    } else if (field.options.length > 0) {
      metaParts.push(`${field.options.length} opcji`);
    }
  }
  if ('defaultValueType' in field && field.defaultValueType === 'EXTERNAL_SYSTEM' && field.defaultValue) {
    metaParts.push(`Rejestr: ${field.defaultValue}`);
  } else if ('readOnly' in field && field.readOnly) {
    metaParts.push('Tylko do odczytu');
  }
  if ('states' in field && field.states && field.states.length > 0) {
    metaParts.push(`Warunkowe (${describeStateQuery(field.states[0].query)})`);
  }

  return (
    <div
      onClick={() => onSelect?.(field.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect?.(field.id);
        }
      }}
      data-testid={`field-card-${field.techName}`}
      className={`group rounded-xl border border-l-4 bg-white p-4 text-left transition-all ${style.border} ${
        isSelected
          ? 'border-blue-600 bg-blue-50/25 ring-2 ring-blue-600/20 shadow-sm'
          : hasErrors
            ? 'border-rose-300 hover:border-rose-400'
            : 'border-slate-200 hover:border-slate-300 hover:shadow-xs'
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[15px] font-semibold leading-snug text-slate-900">
              {title}
              {isRequired && <span className="ml-1 text-rose-600">*</span>}
            </span>
            {isModified && <Badge variant="info">Zmieniono</Badge>}
            {isReviewed && (
              <Badge variant="success">
                <Check className="h-3 w-3" />
                Sprawdzone
              </Badge>
            )}
          </div>

          <p className="text-xs leading-5 text-slate-500">{metaParts.join(' · ')}</p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {assessment && (
            <ConfidenceBadge
              level={assessment.level}
              confidence={assessment.confidence}
              signals={assessment.signals}
              expandable
            />
          )}
          {onToggleReviewed && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onToggleReviewed(field.id);
              }}
              title={
                isReviewed ? 'Cofnij oznaczenie jako sprawdzone' : 'Oznacz pole jako sprawdzone'
              }
              className={`flex h-8 w-8 items-center justify-center rounded-lg border transition-colors ${
                isReviewed
                  ? 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                  : 'border-slate-200 bg-white text-slate-400 hover:border-slate-300 hover:text-slate-700'
              }`}
            >
              <Check className="h-4 w-4" />
            </button>
          )}
          <ChevronRight className="h-4 w-4 text-slate-300 transition-transform group-hover:translate-x-0.5 group-hover:text-blue-600" />
        </div>
      </div>

      {/* Zwięzła linia źródła w dokumencie */}
      {firstSource && (firstSource.text || firstSource.page) && (
        <div className="mt-2.5 flex items-center gap-1.5 text-xs text-slate-500">
          <MapPin className="h-3.5 w-3.5 shrink-0 text-slate-400" />
          {firstSource.page && (
            <span className="shrink-0 font-medium text-slate-600">Str. {firstSource.page}</span>
          )}
          {firstSource.text && <span className="truncate">· „{firstSource.text}”</span>}
        </div>
      )}

      {/* Tylko istotne ostrzeżenia / błędy */}
      {allIssues.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {allIssues.map((issue, idx) => (
            <li
              key={`${issue.code}-${idx}`}
              className={`flex items-start gap-2 rounded-lg px-3 py-2 text-xs leading-relaxed ${
                issue.severity === 'ERROR'
                  ? 'bg-rose-50 text-rose-900'
                  : issue.severity === 'WARNING'
                    ? 'bg-amber-50 text-amber-950'
                    : 'bg-blue-50 text-blue-900'
              }`}
            >
              {issue.severity === 'ERROR' ? (
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-600" />
              ) : (
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
              )}
              <span>{issue.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
