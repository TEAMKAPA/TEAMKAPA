import {
  FIELD_TYPE_LABELS,
  VALIDATOR_LABELS,
  defaultValueToString,
  getFieldTitle,
  isChoiceField,
  isEmptyHtml,
  type LeafField,
  type Validator,
} from '../../types/coiForm';
import type { FieldAssessment, Issue } from '../../types/auditReport';
import { type AnswerValue } from '../../lib/conditionEvaluator';
import { Input, Select, Textarea } from '../ui/Input';

interface FieldFactoryProps {
  field: LeafField;
  mode: 'review' | 'interactive';
  assessment?: FieldAssessment;
  liveIssues?: Issue[];
  isSelected?: boolean;
  isReviewed?: boolean;
  isModified?: boolean;
  onSelect?: (fieldId: string) => void;
  onOpenDetails?: (fieldId: string) => void;
  onUpdateField?: (updated: LeafField) => void;
  onToggleReviewed?: (fieldId: string) => void;
  value?: AnswerValue;
  onChangeValue?: (val: AnswerValue) => void;
  dynamicReadOnly?: boolean;
  validationError?: string;
}

const SAMPLE_DICT_VALUES: Record<string, string[]> = {
  Kraje: ['Polska', 'Niemcy', 'Czechy', 'Słowacja', 'Ukraina', 'Litwa'],
  Województwo: ['mazowieckie', 'małopolskie', 'dolnośląskie', 'pomorskie', 'śląskie', 'wielkopolskie'],
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
  onOpenDetails,
  onUpdateField,
  value,
  onChangeValue,
  dynamicReadOnly = false,
  validationError,
}: FieldFactoryProps) {
  const title = getFieldTitle(field);
  const readOnly = ('readOnly' in field && Boolean(field.readOnly)) || dynamicReadOnly;
  const validators: readonly Validator[] =
    'validation' in field && Array.isArray(field.validation) ? field.validation : [];
  const isRequired = validators.includes('REQUIRED');

  // ---------------------------------------------------------------- Tryb podglądu wypełniania
  if (mode === 'interactive') {
    return (
      <div className="space-y-2 py-4">
        {field.type !== 'INFO_SECTION' && field.type !== 'CONSENT_SECTION' && (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className="text-[15px] font-normal leading-6 text-[#0D0E0E]">
              {title}
              {isRequired && <span className="ml-1 text-[#CA0049]">*</span>}
            </label>
            {readOnly && (
              <span className="text-xs font-normal text-[#52555A]">Tylko do odczytu</span>
            )}
          </div>
        )}

        {'description' in field &&
          !isEmptyHtml(field.description) &&
          field.type !== 'INFO_SECTION' &&
          field.type !== 'TEXT_BLOCK' && (
            <p className="text-sm font-normal text-[#52555A]">{field.description}</p>
          )}

        {field.type === 'TEXT_FIELD' && (
          <Input
            type="text"
            value={String(value ?? defaultValueToString(field.defaultValue))}
            disabled={readOnly}
            hasError={Boolean(validationError)}
            onChange={(e) => onChangeValue?.(e.target.value)}
            placeholder={field.tooltipMessage || title}
          />
        )}

        {field.type === 'NUMBER' && (
          <Input
            type="number"
            value={String(value ?? '')}
            disabled={readOnly}
            hasError={Boolean(validationError)}
            onChange={(e) => onChangeValue?.(e.target.value)}
          />
        )}

        {field.type === 'TEXTAREA' && (
          <Textarea
            rows={3}
            value={String(value ?? defaultValueToString(field.defaultValue))}
            disabled={readOnly}
            hasError={Boolean(validationError)}
            onChange={(e) => onChangeValue?.(e.target.value)}
          />
        )}

        {field.type === 'DATEPICKER' && (
          <Input
            type="date"
            value={String(value ?? '')}
            disabled={readOnly}
            hasError={Boolean(validationError)}
            onChange={(e) => onChangeValue?.(e.target.value)}
          />
        )}

        {field.type === 'SELECT' && (
          <Select
            value={String(value ?? defaultValueToString(field.defaultValue))}
            disabled={readOnly}
            hasError={Boolean(validationError)}
            onChange={(e) => onChangeValue?.(e.target.value)}
          >
            <option value="">Wybierz z listy</option>
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
          </Select>
        )}

        {field.type === 'RADIO' && (
          <div className="space-y-2 pt-1">
            {field.options.map((opt) => {
              const checked = String(value ?? '') === opt.name;
              return (
                <label
                  key={opt.id}
                  className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3.5 py-2.5 text-[15px] font-normal text-[#0D0E0E] ${
                    checked
                      ? 'border-[#08255E] bg-[#EEF2FA]'
                      : 'border-[#E5E7EB] bg-[#FFFFFF] hover:bg-[#F3F3F4]'
                  }`}
                >
                  <input
                    type="radio"
                    name={`preview-${field.id}`}
                    value={opt.name}
                    checked={checked}
                    disabled={readOnly}
                    onChange={() => onChangeValue?.(opt.name)}
                    className="h-4 w-4 accent-[#08255E]"
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
                  className="flex cursor-pointer items-center gap-3 rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-3.5 py-2.5 text-[15px] font-normal text-[#0D0E0E] hover:bg-[#F3F3F4]"
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
                    className="h-4 w-4 accent-[#08255E]"
                  />
                  <span>{opt.name}</span>
                </label>
              );
            })}
          </div>
        )}

        {field.type === 'FILE_UPLOADER' && (
          <label className="flex cursor-pointer items-center justify-center rounded-lg border border-dashed border-[#9FA1A5] bg-[#F3F3F4] px-4 py-3 text-sm font-normal text-[#52555A] hover:border-[#08255E]">
            <span>{value ? String(value) : 'Wybierz plik załącznika'}</span>
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
          <div className="rounded-lg border border-[#E5E7EB] bg-[#F3F3F4] p-3.5 text-[15px] font-normal text-[#0D0E0E]">
            {field.description ?? field.title}
          </div>
        )}

        {field.type === 'INFO_SECTION' && (
          <div className="rounded-lg border border-[#E5E7EB] bg-[#EEF2FA] p-3.5 text-[15px] font-normal text-[#0D0E0E]">
            {field.description}
          </div>
        )}

        {field.type === 'CONSENT_SECTION' && (
          <div className="space-y-2">
            {field.consentSectionConfig.consents.map((consent, idx) => {
              const checkedList = Array.isArray(value) ? value : [];
              const key = String(idx);
              const checked = checkedList.includes(key);
              return (
                <label
                  key={idx}
                  className="flex cursor-pointer items-start gap-3 rounded-lg border border-[#E5E7EB] bg-[#F3F3F4] p-3.5 text-[15px] font-normal text-[#0D0E0E]"
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
                    className="mt-1 h-4 w-4 accent-[#08255E]"
                  />
                  <span>
                    {consent.description}
                    {consent.required && <span className="ml-1 text-[#CA0049]">*</span>}
                  </span>
                </label>
              );
            })}
          </div>
        )}

        {validationError && (
          <p className="text-sm font-normal text-[#CA0049]">{validationError}</p>
        )}
      </div>
    );
  }

  // ---------------------------------------------------------------- Tryb weryfikacji (prosta lista z edycją i pewnością)
  const allIssues = [...(assessment?.issues ?? []), ...liveIssues];
  const isLowConfidence = assessment?.level === 'LOW';
  const confidencePct = assessment ? Math.round(assessment.confidence * 100) : null;

  const metaParts: string[] = [FIELD_TYPE_LABELS[field.type]];
  if (isRequired) metaParts.push('wymagane');
  const domainValidators = validators.filter((v) => v !== 'REQUIRED');
  if (domainValidators.length > 0) {
    metaParts.push(domainValidators.map((v) => VALIDATOR_LABELS[v]).join(', '));
  }
  if (isChoiceField(field)) {
    if (field.dictionaryName) metaParts.push(`Słownik: ${field.dictionaryName}`);
    else if (field.options.length > 0) metaParts.push(`${field.options.length} opcji`);
  }

  const editableValue =
    field.type === 'INFO_SECTION'
      ? field.description
      : field.type === 'CONSENT_SECTION'
        ? (field.consentSectionConfig.consents[0]?.description ?? '')
        : ('title' in field ? (field.title ?? '') : '');

  const handleInlineValueChange = (nextVal: string) => {
    if (!onUpdateField) return;
    if (field.type === 'INFO_SECTION') {
      onUpdateField({ ...field, description: nextVal });
    } else if (field.type === 'CONSENT_SECTION') {
      const nextConsents = field.consentSectionConfig.consents.map((c, i) =>
        i === 0 ? { ...c, description: nextVal } : c,
      );
      onUpdateField({
        ...field,
        consentSectionConfig: { ...field.consentSectionConfig, consents: nextConsents },
      });
    } else {
      onUpdateField({ ...field, title: nextVal } as LeafField);
    }
  };

  return (
    <div
      onClick={() => onSelect?.(field.id)}
      data-testid={`field-card-${field.techName}`}
      className={`cursor-pointer py-2.5 transition-colors ${
        isSelected ? 'border-l-2 border-l-[#08255E] pl-3' : ''
      }`}
    >
      <div className="flex flex-col gap-2 sm:grid sm:grid-cols-[minmax(0,420px)_1fr_auto_auto] sm:items-center sm:gap-4">
        <input
          id={`inline-field-${field.id}`}
          type="text"
          value={editableValue}
          onFocus={() => onSelect?.(field.id)}
          onClick={(e) => {
            e.stopPropagation();
            onSelect?.(field.id);
          }}
          onChange={(e) => handleInlineValueChange(e.target.value)}
          aria-label={`Etykieta pola ${title}`}
          className={`w-full rounded-lg border bg-[#FFFFFF] px-3 py-2 text-[15px] font-normal text-[#0D0E0E] shadow-none focus:outline-none ${
            isLowConfidence
              ? 'border-[#CA0049] focus:border-[#CA0049]'
              : 'border-[#E5E7EB] focus:border-[#08255E]'
          }`}
        />

        <div className="flex flex-wrap items-center gap-2 text-[14px] font-normal text-[#374151]">
          <span>{metaParts.join(' · ')}</span>
          {isModified && <span className="text-[#08255E]">Zmieniono</span>}
          {isReviewed && <span className="text-[#065F46]">Sprawdzone</span>}
        </div>

        <div className="flex items-center justify-between gap-4 sm:contents">
          {confidencePct !== null ? (
            <span
              className={`text-[14px] font-normal tabular-nums ${
                isLowConfidence ? 'text-[#CA0049]' : 'text-[#374151]'
              }`}
            >
              Pewność: {confidencePct}%
            </span>
          ) : (
            <span />
          )}

          {onOpenDetails && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onOpenDetails(field.id);
              }}
              className="shrink-0 text-[15px] font-normal text-[#08255E] hover:underline"
            >
              Szczegóły
            </button>
          )}
        </div>
      </div>

      {allIssues.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {allIssues.map((issue, idx) => (
            <li
              key={`${issue.code}-${idx}`}
              className={`text-[14px] font-normal ${
                issue.severity === 'ERROR'
                  ? 'text-[#CA0049]'
                  : issue.severity === 'WARNING'
                    ? 'text-[#92400E]'
                    : 'text-[#374151]'
              }`}
            >
              {issue.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
