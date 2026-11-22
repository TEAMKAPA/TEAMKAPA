import { useEffect, useState } from 'react';
import {
  ALL_VALIDATORS,
  COI_DICTIONARIES,
  FIELD_TYPE_LABELS,
  INITIAL_DATA_LIST,
  TECH_NAME_REGEX,
  VALIDATOR_LABELS,
  getFieldTitle,
  isChoiceField,
  type LeafField,
  type LeafFieldType,
  type Option,
  type State,
  type Validator,
} from '../../types/coiForm';
import type { FieldAssessment, Issue } from '../../types/auditReport';
import { Button } from '../ui/Button';
import { Input, Select, Textarea } from '../ui/Input';

interface FieldEditorDrawerProps {
  field: LeafField;
  assessment?: FieldAssessment;
  liveIssues: Issue[];
  isReviewed: boolean;
  isModified: boolean;
  onUpdateField: (updated: LeafField) => void;
  onResetField: (fieldId: string) => void;
  onToggleReviewed: (fieldId: string) => void;
  onFocusOnDocument?: () => void;
  onClose: () => void;
}

const LEAF_TYPES: readonly LeafFieldType[] = [
  'TEXT_FIELD',
  'TEXTAREA',
  'NUMBER',
  'DATEPICKER',
  'SELECT',
  'RADIO',
  'CHECKBOX',
  'FILE_UPLOADER',
  'CONSENT_SECTION',
  'TEXT_BLOCK',
  'INFO_SECTION',
];

function slugifyTechName(input: string): string {
  const ascii = input
    .toLowerCase()
    .replace(/ł/g, 'l')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  if (!ascii) return 'opcja';
  return /^[a-z_]/.test(ascii) ? ascii : `o_${ascii}`;
}

function generateId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `opt-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function convertFieldType(field: LeafField, nextType: LeafFieldType): LeafField {
  if (field.type === nextType) return field;
  const base = {
    id: field.id,
    techName: field.techName,
    questionOrder: field.questionOrder,
    tooltipMessage: field.tooltipMessage,
  };
  const title = 'title' in field ? field.title : undefined;
  const description = 'description' in field ? field.description : undefined;
  const readOnly = 'readOnly' in field ? field.readOnly : undefined;
  const states = 'states' in field ? field.states : undefined;
  const defaultValue = 'defaultValue' in field ? field.defaultValue : undefined;
  const defaultValueType = 'defaultValueType' in field ? field.defaultValueType : undefined;
  const isRequired =
    'validation' in field && (field.validation as readonly Validator[]).includes('REQUIRED');
  const reqOnly: 'REQUIRED'[] = isRequired ? ['REQUIRED'] : [];
  const existingOptions: Option[] = isChoiceField(field)
    ? field.options
    : [{ id: generateId(), name: 'Opcja 1', techName: 'opcja_1' }];
  const existingDict = isChoiceField(field) ? field.dictionaryName : undefined;

  switch (nextType) {
    case 'TEXT_FIELD':
      return { ...base, type: 'TEXT_FIELD', title, description, readOnly, defaultValue, defaultValueType, validation: isRequired ? ['REQUIRED'] : [], states };
    case 'TEXTAREA':
      return { ...base, type: 'TEXTAREA', title, description, readOnly, defaultValue, defaultValueType, validation: reqOnly, states };
    case 'NUMBER':
      return { ...base, type: 'NUMBER', title, description, readOnly, validation: reqOnly, states };
    case 'DATEPICKER':
      return { ...base, type: 'DATEPICKER', title, description, readOnly, defaultValue, defaultValueType, validation: reqOnly, states };
    case 'SELECT':
      return { ...base, type: 'SELECT', title, description, readOnly, options: existingDict ? [] : existingOptions, dictionaryName: existingDict, defaultValue, defaultValueType, validation: reqOnly, states };
    case 'RADIO':
      return { ...base, type: 'RADIO', title, description, readOnly, options: existingDict === 'Numery kierunkowe' ? [] : existingOptions, dictionaryName: existingDict === 'Numery kierunkowe' ? existingDict : undefined, validation: reqOnly, states };
    case 'CHECKBOX':
      return { ...base, type: 'CHECKBOX', title, description, readOnly, options: existingDict === 'Numery kierunkowe' ? [] : existingOptions, dictionaryName: existingDict === 'Numery kierunkowe' ? existingDict : undefined, validation: reqOnly, states };
    case 'FILE_UPLOADER':
      return { ...base, type: 'FILE_UPLOADER', title, description, defaultValue, validation: reqOnly, states };
    case 'TEXT_BLOCK':
      return { ...base, type: 'TEXT_BLOCK', title, description, states: states?.filter((s): s is { type: 'SHOW'; query: string } => s.type === 'SHOW') };
    case 'CONSENT_SECTION':
      return { ...base, type: 'CONSENT_SECTION', consentSectionConfig: { consents: [{ description: description || title || 'Treść zgody', required: true }], required: true, files: [] } };
    case 'INFO_SECTION':
      return { ...base, type: 'INFO_SECTION', description: description || title || 'Informacja dla wnioskodawcy' };
  }
}

export function FieldEditorDrawer({
  field,
  assessment,
  liveIssues,
  isReviewed,
  isModified,
  onUpdateField,
  onResetField,
  onToggleReviewed,
  onFocusOnDocument,
  onClose,
}: FieldEditorDrawerProps) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const techNameValid = TECH_NAME_REGEX.test(field.techName);
  const supportsTitle = field.type !== 'CONSENT_SECTION' && field.type !== 'INFO_SECTION';
  const supportsDescription = field.type !== 'CONSENT_SECTION';
  const supportsReadOnly =
    field.type !== 'FILE_UPLOADER' &&
    field.type !== 'TEXT_BLOCK' &&
    field.type !== 'CONSENT_SECTION' &&
    field.type !== 'INFO_SECTION';
  const supportsDefaultValue =
    field.type === 'TEXT_FIELD' ||
    field.type === 'SELECT' ||
    field.type === 'TEXTAREA' ||
    field.type === 'DATEPICKER';
  const supportsStates = field.type !== 'CONSENT_SECTION' && field.type !== 'INFO_SECTION';

  const allowedDictionaries = isChoiceField(field)
    ? Object.entries(COI_DICTIONARIES)
        .filter(([, allowedTypes]) => allowedTypes.includes(field.type))
        .map(([name]) => name)
    : [];

  const handleToggleValidator = (validator: Validator) => {
    if (!('validation' in field)) return;
    if (field.type === 'TEXT_FIELD') {
      const next = field.validation.includes(validator)
        ? field.validation.filter((v) => v !== validator)
        : [...field.validation, validator];
      onUpdateField({ ...field, validation: next });
    } else if (validator === 'REQUIRED') {
      const hasReq = field.validation.includes('REQUIRED');
      onUpdateField({ ...field, validation: hasReq ? [] : ['REQUIRED'] } as LeafField);
    }
  };

  const handleStateChange = (index: number, patch: Partial<State>) => {
    if (!('states' in field)) return;
    const list: State[] = [...(field.states ?? [])];
    list[index] = { ...list[index], ...patch };
    if (field.type === 'TEXT_BLOCK') {
      onUpdateField({
        ...field,
        states: list.map((s) => ({ type: 'SHOW' as const, query: s.query })),
      });
    } else {
      onUpdateField({ ...field, states: list } as LeafField);
    }
  };

  const handleAddState = () => {
    if (!('states' in field)) return;
    const list: State[] = [...(field.states ?? []), { type: 'SHOW', query: '' }];
    if (field.type === 'TEXT_BLOCK') {
      onUpdateField({
        ...field,
        states: list.map((s) => ({ type: 'SHOW' as const, query: s.query })),
      });
    } else {
      onUpdateField({ ...field, states: list } as LeafField);
    }
  };

  const handleRemoveState = (index: number) => {
    if (!('states' in field)) return;
    const list: State[] = (field.states ?? []).filter((_, i) => i !== index);
    if (field.type === 'TEXT_BLOCK') {
      onUpdateField({
        ...field,
        states:
          list.length > 0
            ? list.map((s) => ({ type: 'SHOW' as const, query: s.query }))
            : undefined,
      });
    } else {
      onUpdateField({ ...field, states: list.length > 0 ? list : undefined } as LeafField);
    }
  };

  return (
    <aside
      aria-label="Szczegóły pola"
      data-testid="field-editor-panel"
      className="fixed inset-y-0 right-0 z-40 flex w-full max-w-xl flex-col border-l border-[#E5E7EB] bg-[#FFFFFF] shadow-none"
    >
      <div className="flex items-start justify-between gap-3 border-b border-[#E5E7EB] px-6 py-4">
        <div className="min-w-0">
          <h2 className="truncate text-[20px] font-normal leading-7 text-[#0D0E0E]">
            {getFieldTitle(field)}
          </h2>
          {assessment && (
            <p className="mt-0.5 text-sm font-normal text-[#52555A]">
              Pewność: {Math.round(assessment.confidence * 100)}%
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Zamknij edytor pola"
          className="rounded-lg border border-[#E5E7EB] px-3 py-1.5 text-sm font-normal text-[#0D0E0E] hover:bg-[#F3F3F4]"
        >
          Zamknij
        </button>
      </div>

      <div className="thin-scrollbar flex-1 space-y-5 overflow-y-auto px-6 py-5 text-[15px] font-normal">
        {liveIssues.length > 0 && (
          <div className="space-y-1 rounded-lg border border-[#CA0049] bg-[#FFF1F2] p-3.5 text-sm text-[#9F1239]">
            {liveIssues.map((iss, idx) => (
              <div key={`${iss.code}-${idx}`}>
                {iss.severity}: {iss.message}
              </div>
            ))}
          </div>
        )}

        {supportsTitle && (
          <label className="block space-y-1.5">
            <span className="text-sm font-normal text-[#0D0E0E]">Etykieta pola</span>
            <Input
              type="text"
              aria-label="Etykieta pola"
              value={'title' in field ? (field.title ?? '') : ''}
              onChange={(e) => onUpdateField({ ...field, title: e.target.value } as LeafField)}
            />
          </label>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block space-y-1.5">
            <span className="text-sm font-normal text-[#0D0E0E]">Typ pola</span>
            <Select
              aria-label="Typ pola"
              value={field.type}
              onChange={(e) =>
                onUpdateField(convertFieldType(field, e.target.value as LeafFieldType))
              }
            >
              {LEAF_TYPES.map((t) => (
                <option key={t} value={t}>
                  {FIELD_TYPE_LABELS[t]}
                </option>
              ))}
            </Select>
          </label>

          <label className="block space-y-1.5">
            <span className="text-sm font-normal text-[#0D0E0E]">Nazwa techniczna</span>
            <Input
              type="text"
              aria-label="Nazwa techniczna pola"
              value={field.techName}
              hasError={!techNameValid}
              onChange={(e) => onUpdateField({ ...field, techName: e.target.value } as LeafField)}
              className="font-mono"
            />
          </label>
        </div>

        {supportsDescription && (
          <label className="block space-y-1.5">
            <span className="text-sm font-normal text-[#0D0E0E]">Opis pomocniczy</span>
            <Textarea
              rows={2}
              value={'description' in field ? (field.description ?? '') : ''}
              onChange={(e) =>
                onUpdateField({ ...field, description: e.target.value } as LeafField)
              }
            />
          </label>
        )}

        {'validation' in field && (
          <div className="space-y-2 border-t border-[#E5E7EB] pt-4">
            <div className="text-sm font-normal text-[#52555A]">Walidacja</div>
            <div className="flex flex-wrap gap-2">
              {(field.type === 'TEXT_FIELD' ? ALL_VALIDATORS : (['REQUIRED'] as const)).map(
                (validator) => {
                  const checked = (field.validation as Validator[]).includes(validator);
                  return (
                    <button
                      key={validator}
                      type="button"
                      onClick={() => handleToggleValidator(validator)}
                      className={`rounded-lg border px-3 py-1.5 text-sm font-normal ${
                        checked
                          ? 'border-[#08255E] bg-[#08255E] text-white'
                          : 'border-[#E5E7EB] bg-[#FFFFFF] text-[#0D0E0E] hover:bg-[#F3F3F4]'
                      }`}
                    >
                      {VALIDATOR_LABELS[validator]}
                    </button>
                  );
                },
              )}
            </div>
          </div>
        )}

        {isChoiceField(field) && (
          <div className="space-y-3 border-t border-[#E5E7EB] pt-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-normal text-[#52555A]">Opcje wyboru</span>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => {
                    if (field.dictionaryName) {
                      onUpdateField({
                        ...field,
                        dictionaryName: undefined,
                        options:
                          field.options.length > 0
                            ? field.options
                            : [{ id: generateId(), name: 'Opcja 1', techName: 'opcja_1' }],
                      } as LeafField);
                    }
                  }}
                  className={`rounded-lg border px-3 py-1 text-xs font-normal ${
                    !field.dictionaryName
                      ? 'border-[#08255E] bg-[#08255E] text-white'
                      : 'border-[#E5E7EB] text-[#0D0E0E]'
                  }`}
                >
                  Własne
                </button>
                <button
                  type="button"
                  disabled={allowedDictionaries.length === 0}
                  onClick={() => {
                    const firstDict = allowedDictionaries[0];
                    if (firstDict) {
                      onUpdateField({ ...field, dictionaryName: firstDict, options: [] } as LeafField);
                    }
                  }}
                  className={`rounded-lg border px-3 py-1 text-xs font-normal disabled:opacity-40 ${
                    field.dictionaryName
                      ? 'border-[#08255E] bg-[#08255E] text-white'
                      : 'border-[#E5E7EB] text-[#0D0E0E]'
                  }`}
                >
                  Słownik
                </button>
              </div>
            </div>

            {field.dictionaryName ? (
              <Select
                value={field.dictionaryName}
                onChange={(e) =>
                  onUpdateField({ ...field, dictionaryName: e.target.value, options: [] } as LeafField)
                }
              >
                {allowedDictionaries.map((dict) => (
                  <option key={dict} value={dict}>
                    {dict}
                  </option>
                ))}
              </Select>
            ) : (
              <div className="space-y-2">
                {field.options.map((opt, idx) => (
                  <div key={opt.id} className="grid grid-cols-[1fr_1fr_auto] gap-2">
                    <Input
                      type="text"
                      value={opt.name}
                      onChange={(e) => {
                        const next = field.options.map((o, i) =>
                          i === idx
                            ? { ...o, name: e.target.value, techName: slugifyTechName(e.target.value) }
                            : o,
                        );
                        onUpdateField({ ...field, options: next } as LeafField);
                      }}
                    />
                    <Input
                      type="text"
                      value={opt.techName ?? ''}
                      onChange={(e) => {
                        const next = field.options.map((o, i) =>
                          i === idx ? { ...o, techName: e.target.value } : o,
                        );
                        onUpdateField({ ...field, options: next } as LeafField);
                      }}
                      className="font-mono text-xs"
                    />
                    <button
                      type="button"
                      disabled={field.options.length <= 1}
                      onClick={() =>
                        onUpdateField({
                          ...field,
                          options: field.options.filter((_, i) => i !== idx),
                        } as LeafField)
                      }
                      className="rounded-lg border border-[#E5E7EB] px-2.5 text-sm text-[#52555A] hover:text-[#CA0049] disabled:opacity-30"
                    >
                      Usuń
                    </button>
                  </div>
                ))}
                <Button
                  variant="secondary"
                  size="xs"
                  onClick={() =>
                    onUpdateField({
                      ...field,
                      options: [
                        ...field.options,
                        { id: generateId(), name: `Opcja ${field.options.length + 1}`, techName: `opcja_${field.options.length + 1}` },
                      ],
                    } as LeafField)
                  }
                >
                  Dodaj opcję
                </Button>
              </div>
            )}
          </div>
        )}

        <div className="border-t border-[#E5E7EB] pt-4">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="flex w-full items-center justify-between rounded-lg border border-[#E5E7EB] bg-[#F3F3F4] px-4 py-2 text-left text-sm font-normal text-[#0D0E0E]"
          >
            <span>Ustawienia zaawansowane</span>
            <span>{showAdvanced ? 'Ukryj' : 'Pokaż'}</span>
          </button>

          {showAdvanced && (
            <div className="mt-4 space-y-4">
              {supportsReadOnly && (
                <label className="flex items-center gap-2 text-sm font-normal text-[#0D0E0E]">
                  <input
                    type="checkbox"
                    checked={'readOnly' in field ? Boolean(field.readOnly) : false}
                    onChange={(e) =>
                      onUpdateField({ ...field, readOnly: e.target.checked ? true : undefined } as LeafField)
                    }
                    className="h-4 w-4 accent-[#08255E]"
                  />
                  <span>Tylko do odczytu</span>
                </label>
              )}

              {supportsDefaultValue && (
                <div className="space-y-2 rounded-lg border border-[#E5E7EB] bg-[#F3F3F4] p-3.5">
                  <label className="flex items-center gap-2 text-sm font-normal text-[#0D0E0E]">
                    <input
                      type="checkbox"
                      checked={'defaultValueType' in field && field.defaultValueType === 'EXTERNAL_SYSTEM'}
                      onChange={(e) => {
                        if (e.target.checked) {
                          onUpdateField({
                            ...field,
                            defaultValueType: 'EXTERNAL_SYSTEM',
                            // Dane inicjalne to zawsze tekst „ŹRÓDŁO.atrybut” — nie lista ze słownika.
                            defaultValue:
                              ('defaultValue' in field &&
                                typeof field.defaultValue === 'string' &&
                                field.defaultValue) ||
                              'MLPZ.name',
                            readOnly: true,
                          } as LeafField);
                        } else {
                          onUpdateField({ ...field, defaultValueType: undefined } as LeafField);
                        }
                      }}
                      className="h-4 w-4 accent-[#08255E]"
                    />
                    <span>Dane z rejestru (EXTERNAL_SYSTEM)</span>
                  </label>

                  {'defaultValueType' in field && field.defaultValueType === 'EXTERNAL_SYSTEM' ? (
                    <Select
                      value={
                        'defaultValue' in field
                          ? Array.isArray(field.defaultValue)
                            ? (field.defaultValue[0] ?? '')
                            : (field.defaultValue ?? '')
                          : ''
                      }
                      onChange={(e) =>
                        onUpdateField({ ...field, defaultValue: e.target.value } as LeafField)
                      }
                    >
                      {INITIAL_DATA_LIST.map((item) => (
                        <option key={item.reference} value={item.reference}>
                          {item.reference} ({item.label})
                        </option>
                      ))}
                    </Select>
                  ) : (
                    <Input
                      type="text"
                      value={
                        'defaultValue' in field
                          ? Array.isArray(field.defaultValue)
                            ? field.defaultValue.join(', ')
                            : (field.defaultValue ?? '')
                          : ''
                      }
                      onChange={(e) =>
                        onUpdateField({ ...field, defaultValue: e.target.value || undefined } as LeafField)
                      }
                      placeholder="Wartość domyślna"
                    />
                  )}
                </div>
              )}

              {supportsStates && 'states' in field && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-normal text-[#0D0E0E]">
                      Warunki ({field.states?.length ?? 0})
                    </span>
                    <Button variant="secondary" size="xs" onClick={handleAddState}>
                      Dodaj warunek
                    </Button>
                  </div>
                  {(field.states ?? []).map((st, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      <Select
                        value={st.type}
                        disabled={field.type === 'TEXT_BLOCK'}
                        onChange={(e) => handleStateChange(idx, { type: e.target.value as State['type'] })}
                        className="w-32"
                      >
                        <option value="SHOW">SHOW</option>
                        {field.type !== 'TEXT_BLOCK' && <option value="READ_ONLY">READ_ONLY</option>}
                      </Select>
                      <Input
                        type="text"
                        value={st.query}
                        onChange={(e) => handleStateChange(idx, { query: e.target.value })}
                        className="flex-1 font-mono text-xs"
                      />
                      <button
                        type="button"
                        onClick={() => handleRemoveState(idx)}
                        className="rounded-lg border border-[#E5E7EB] px-2.5 py-2 text-xs text-[#52555A] hover:text-[#CA0049]"
                      >
                        Usuń
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#E5E7EB] bg-[#FFFFFF] px-6 py-4">
        <div className="flex items-center gap-2">
          {onFocusOnDocument && assessment?.sources[0]?.bbox && (
            <Button variant="secondary" size="xs" onClick={onFocusOnDocument}>
              Pokaż źródło
            </Button>
          )}
          {isModified && (
            <Button variant="secondary" size="xs" onClick={() => onResetField(field.id)}>
              Cofnij zmiany
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant={isReviewed ? 'navy' : 'secondary'}
            size="sm"
            onClick={() => onToggleReviewed(field.id)}
          >
            {isReviewed ? 'Sprawdzone' : 'Oznacz jako sprawdzone'}
          </Button>
          <Button variant="navy" size="sm" onClick={onClose}>
            Gotowe
          </Button>
        </div>
      </div>
    </aside>
  );
}
