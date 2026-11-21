import { useEffect, useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronUp,
  MapPin,
  Plus,
  RotateCcw,
  Trash2,
  X,
} from 'lucide-react';
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
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { ConfidenceBadge } from '../audit/ConfidenceBadge';

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
      return {
        ...base,
        type: 'TEXT_FIELD',
        title,
        description,
        readOnly,
        defaultValue,
        defaultValueType,
        validation: isRequired ? ['REQUIRED'] : [],
        states,
      };
    case 'TEXTAREA':
      return {
        ...base,
        type: 'TEXTAREA',
        title,
        description,
        readOnly,
        defaultValue,
        defaultValueType,
        validation: reqOnly,
        states,
      };
    case 'NUMBER':
      return {
        ...base,
        type: 'NUMBER',
        title,
        description,
        readOnly,
        validation: reqOnly,
        states,
      };
    case 'DATEPICKER':
      return {
        ...base,
        type: 'DATEPICKER',
        title,
        description,
        readOnly,
        defaultValue,
        defaultValueType,
        validation: reqOnly,
        states,
      };
    case 'SELECT':
      return {
        ...base,
        type: 'SELECT',
        title,
        description,
        readOnly,
        options: existingDict ? [] : existingOptions,
        dictionaryName: existingDict,
        defaultValue,
        defaultValueType,
        validation: reqOnly,
        states,
      };
    case 'RADIO':
      return {
        ...base,
        type: 'RADIO',
        title,
        description,
        readOnly,
        options: existingDict === 'Numery kierunkowe' ? [] : existingOptions,
        dictionaryName: existingDict === 'Numery kierunkowe' ? existingDict : undefined,
        validation: reqOnly,
        states,
      };
    case 'CHECKBOX':
      return {
        ...base,
        type: 'CHECKBOX',
        title,
        description,
        readOnly,
        options: existingDict === 'Numery kierunkowe' ? [] : existingOptions,
        dictionaryName: existingDict === 'Numery kierunkowe' ? existingDict : undefined,
        validation: reqOnly,
        states,
      };
    case 'FILE_UPLOADER':
      return {
        ...base,
        type: 'FILE_UPLOADER',
        title,
        description,
        defaultValue,
        validation: reqOnly,
        states,
      };
    case 'TEXT_BLOCK':
      return {
        ...base,
        type: 'TEXT_BLOCK',
        title,
        description,
        states: states?.filter((s): s is { type: 'SHOW'; query: string } => s.type === 'SHOW'),
      };
    case 'CONSENT_SECTION':
      return {
        ...base,
        type: 'CONSENT_SECTION',
        consentSectionConfig: {
          consents: [{ description: description || title || 'Treść zgody', required: true }],
          required: true,
          files: [],
        },
      };
    case 'INFO_SECTION':
      return {
        ...base,
        type: 'INFO_SECTION',
        description: description || title || 'Informacja dla wnioskodawcy',
      };
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
      const current = field.validation;
      const next = current.includes(validator)
        ? current.filter((v) => v !== validator)
        : [...current, validator];
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

  const inputClass =
    'w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-sm text-slate-900 focus:border-blue-600 focus:ring-2 focus:ring-blue-600/15 focus:outline-none';

  return (
    <aside
      aria-label="Panel edycji właściwości pola"
      data-testid="field-editor-panel"
      className="fixed inset-y-0 right-0 z-40 flex w-full max-w-xl flex-col border-l border-slate-200 bg-white shadow-2xl"
    >
      {/* Nagłówek szuflady */}
      <div className="flex items-start justify-between gap-3 border-b border-slate-200 bg-slate-50 px-6 py-4">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-base font-bold text-slate-900">{getFieldTitle(field)}</h2>
            {assessment && (
              <ConfidenceBadge
                level={assessment.level}
                confidence={assessment.confidence}
                signals={assessment.signals}
                compact
              />
            )}
            {isModified && <Badge variant="info">Zmieniono</Badge>}
          </div>
          <p className="text-xs text-slate-500">
            Edycja konfiguracji pola COI · podgląd dokumentu po lewej pozostaje widoczny
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Zamknij edytor pola"
          className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-200/70 hover:text-slate-700"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      {/* Przewijana zawartość */}
      <div className="thin-scrollbar flex-1 space-y-5 overflow-y-auto px-6 py-5 text-sm">
        {/* Błędy / ostrzeżenia na żywo */}
        {liveIssues.length > 0 && (
          <div className="space-y-1.5 rounded-xl border border-rose-200 bg-rose-50 p-3.5">
            {liveIssues.map((iss, idx) => (
              <div key={`${iss.code}-${idx}`} className="text-xs text-rose-900">
                <strong className="mr-1">{iss.severity}:</strong>
                {iss.message}
              </div>
            ))}
          </div>
        )}

        {/* Sekcja 1: Podstawowe */}
        <div className="space-y-4">
          <div className="text-xs font-semibold tracking-wider text-slate-400 uppercase">
            Dane podstawowe
          </div>

          {supportsTitle && (
            <label className="block space-y-1.5">
              <span className="font-medium text-slate-800">Etykieta widoczna dla obywatela</span>
              <input
                type="text"
                aria-label="Etykieta pola"
                value={'title' in field ? (field.title ?? '') : ''}
                onChange={(e) => onUpdateField({ ...field, title: e.target.value } as LeafField)}
                className={inputClass}
              />
            </label>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block space-y-1.5">
              <span className="font-medium text-slate-800">Typ pola COI</span>
              <select
                aria-label="Typ pola COI"
                value={field.type}
                onChange={(e) =>
                  onUpdateField(convertFieldType(field, e.target.value as LeafFieldType))
                }
                className={inputClass}
              >
                {LEAF_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {FIELD_TYPE_LABELS[t]}
                  </option>
                ))}
              </select>
            </label>

            <label className="block space-y-1.5">
              <span className="font-medium text-slate-800">Nazwa techniczna (techName)</span>
              <input
                type="text"
                aria-label="Nazwa techniczna pola"
                value={field.techName}
                onChange={(e) => onUpdateField({ ...field, techName: e.target.value } as LeafField)}
                className={`w-full rounded-xl border px-3.5 py-2 font-mono text-sm focus:outline-none ${
                  techNameValid
                    ? 'border-slate-300 bg-white focus:border-blue-600 focus:ring-2 focus:ring-blue-600/15'
                    : 'border-rose-400 bg-rose-50/50 focus:border-rose-500'
                }`}
              />
              {!techNameValid && (
                <span className="text-xs text-rose-600">
                  Dozwolone litery A–Z, cyfry i _, bez spacji.
                </span>
              )}
            </label>
          </div>

          {supportsDescription && (
            <label className="block space-y-1.5">
              <span className="font-medium text-slate-800">
                Opis pomocniczy{field.type === 'INFO_SECTION' ? ' *' : ''}
              </span>
              <textarea
                rows={2}
                value={'description' in field ? (field.description ?? '') : ''}
                onChange={(e) =>
                  onUpdateField({ ...field, description: e.target.value } as LeafField)
                }
                className={inputClass}
              />
            </label>
          )}
        </div>

        {/* Sekcja 2: Walidatory */}
        {'validation' in field && (
          <div className="space-y-2.5 border-t border-slate-100 pt-5">
            <div className="text-xs font-semibold tracking-wider text-slate-400 uppercase">
              Wymagalność i walidacja
            </div>
            <div className="flex flex-wrap gap-2">
              {(field.type === 'TEXT_FIELD' ? ALL_VALIDATORS : (['REQUIRED'] as const)).map(
                (validator) => {
                  const checked = (field.validation as Validator[]).includes(validator);
                  return (
                    <button
                      key={validator}
                      type="button"
                      onClick={() => handleToggleValidator(validator)}
                      className={`rounded-xl border px-3 py-1.5 text-xs font-medium transition-colors ${
                        checked
                          ? 'border-blue-600 bg-blue-50 text-blue-900'
                          : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
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

        {/* Sekcja 3: Opcje lub Słownik COI */}
        {isChoiceField(field) && (
          <div className="space-y-3 border-t border-slate-100 pt-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs font-semibold tracking-wider text-slate-400 uppercase">
                Opcje wyboru / Słownik COI
              </div>
              <div className="inline-flex rounded-lg border border-slate-200 bg-slate-100 p-0.5 text-xs">
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
                  className={`rounded-md px-2.5 py-1 font-medium ${
                    !field.dictionaryName ? 'bg-white text-slate-900 shadow-2xs' : 'text-slate-600'
                  }`}
                >
                  Własne opcje
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const firstDict = allowedDictionaries[0];
                    if (firstDict) {
                      onUpdateField({
                        ...field,
                        dictionaryName: firstDict,
                        options: [],
                      } as LeafField);
                    }
                  }}
                  disabled={allowedDictionaries.length === 0}
                  className={`rounded-md px-2.5 py-1 font-medium disabled:opacity-40 ${
                    field.dictionaryName ? 'bg-white text-slate-900 shadow-2xs' : 'text-slate-600'
                  }`}
                >
                  Słownik COI
                </button>
              </div>
            </div>

            {field.dictionaryName ? (
              <div className="space-y-3 rounded-xl bg-slate-50 p-3.5">
                <label className="block space-y-1.5">
                  <span className="text-xs font-medium text-slate-700">Słownik COI</span>
                  <select
                    value={field.dictionaryName}
                    onChange={(e) =>
                      onUpdateField({
                        ...field,
                        dictionaryName: e.target.value,
                        options: [],
                      } as LeafField)
                    }
                    className={inputClass}
                  >
                    {allowedDictionaries.map((dict) => (
                      <option key={dict} value={dict}>
                        {dict}
                      </option>
                    ))}
                    {!allowedDictionaries.includes(field.dictionaryName) && (
                      <option value={field.dictionaryName}>{field.dictionaryName}</option>
                    )}
                  </select>
                </label>
                {field.type === 'SELECT' && (
                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={Boolean(field.searchEnabled)}
                      onChange={(e) =>
                        onUpdateField({ ...field, searchEnabled: e.target.checked || undefined })
                      }
                      className="h-4 w-4"
                    />
                    <span>Włącz wyszukiwarkę na liście (zalecane przy &gt; 15 opcjach)</span>
                  </label>
                )}
              </div>
            ) : (
              <div className="space-y-2">
                {field.options.map((opt, idx) => (
                  <div key={opt.id} className="grid grid-cols-[1fr_1fr_auto] gap-2">
                    <input
                      type="text"
                      value={opt.name}
                      placeholder="Etykieta opcji"
                      onChange={(e) => {
                        const next = field.options.map((o, i) =>
                          i === idx
                            ? {
                                ...o,
                                name: e.target.value,
                                techName: slugifyTechName(e.target.value),
                              }
                            : o,
                        );
                        onUpdateField({ ...field, options: next } as LeafField);
                      }}
                      className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm"
                    />
                    <input
                      type="text"
                      value={opt.techName}
                      placeholder="techName"
                      onChange={(e) => {
                        const next = field.options.map((o, i) =>
                          i === idx ? { ...o, techName: e.target.value } : o,
                        );
                        onUpdateField({ ...field, options: next } as LeafField);
                      }}
                      className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-mono text-xs"
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
                      aria-label="Usuń opcję"
                      className="rounded-lg p-2 text-slate-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-30"
                    >
                      <Trash2 className="h-4 w-4" />
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
                        {
                          id: generateId(),
                          name: `Opcja ${field.options.length + 1}`,
                          techName: `opcja_${field.options.length + 1}`,
                        },
                      ],
                    } as LeafField)
                  }
                >
                  <Plus className="h-3.5 w-3.5" />
                  Dodaj opcję
                </Button>
              </div>
            )}
          </div>
        )}

        {/* Zgody dla CONSENT_SECTION */}
        {field.type === 'CONSENT_SECTION' && (
          <div className="space-y-3 border-t border-slate-100 pt-5">
            <div className="flex items-center justify-between">
              <div className="text-xs font-semibold tracking-wider text-slate-400 uppercase">
                Treść zgód
              </div>
              <label className="flex items-center gap-2 text-xs text-slate-700">
                <input
                  type="checkbox"
                  checked={Boolean(field.selectAllEnabled)}
                  onChange={(e) =>
                    onUpdateField({ ...field, selectAllEnabled: e.target.checked || undefined })
                  }
                />
                Zaznacz wszystkie
              </label>
            </div>
            {field.consentSectionConfig.consents.map((consent, idx) => (
              <div key={idx} className="space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3">
                <textarea
                  rows={2}
                  value={consent.description}
                  onChange={(e) => {
                    const nextConsents = field.consentSectionConfig.consents.map((c, i) =>
                      i === idx ? { ...c, description: e.target.value } : c,
                    );
                    onUpdateField({
                      ...field,
                      consentSectionConfig: {
                        ...field.consentSectionConfig,
                        consents: nextConsents,
                      },
                    });
                  }}
                  className={inputClass}
                />
                <div className="flex items-center justify-between text-xs">
                  <label className="flex items-center gap-2 text-slate-700">
                    <input
                      type="checkbox"
                      checked={Boolean(consent.required)}
                      onChange={(e) => {
                        const nextConsents = field.consentSectionConfig.consents.map((c, i) =>
                          i === idx ? { ...c, required: e.target.checked } : c,
                        );
                        onUpdateField({
                          ...field,
                          consentSectionConfig: {
                            ...field.consentSectionConfig,
                            consents: nextConsents,
                          },
                        });
                      }}
                    />
                    Zgoda obowiązkowa
                  </label>
                  {field.consentSectionConfig.consents.length > 1 && (
                    <button
                      type="button"
                      onClick={() => {
                        const nextConsents = field.consentSectionConfig.consents.filter(
                          (_, i) => i !== idx,
                        );
                        onUpdateField({
                          ...field,
                          consentSectionConfig: {
                            ...field.consentSectionConfig,
                            consents: nextConsents,
                          },
                        });
                      }}
                      className="font-medium text-rose-600 hover:underline"
                    >
                      Usuń zgodę
                    </button>
                  )}
                </div>
              </div>
            ))}
            <Button
              variant="secondary"
              size="xs"
              onClick={() =>
                onUpdateField({
                  ...field,
                  consentSectionConfig: {
                    ...field.consentSectionConfig,
                    consents: [
                      ...field.consentSectionConfig.consents,
                      { description: 'Nowa zgoda', required: false },
                    ],
                  },
                })
              }
            >
              <Plus className="h-3.5 w-3.5" />
              Dodaj zgodę
            </Button>
          </div>
        )}

        {/* Sekcja 4: Zaawansowane (zwijana, żeby nie przytłaczać) */}
        <div className="border-t border-slate-100 pt-4">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="flex w-full items-center justify-between rounded-xl bg-slate-50 px-3.5 py-2.5 text-left text-xs font-semibold text-slate-700 hover:bg-slate-100"
          >
            <span>Ustawienia zaawansowane (rejestr, readOnly, warunki JSONata, tooltip)</span>
            {showAdvanced ? (
              <ChevronUp className="h-4 w-4 text-slate-500" />
            ) : (
              <ChevronDown className="h-4 w-4 text-slate-500" />
            )}
          </button>

          {showAdvanced && (
            <div className="mt-4 space-y-4">
              <label className="block space-y-1.5">
                <span className="text-xs font-medium text-slate-700">Podpowiedź (tooltipMessage)</span>
                <input
                  type="text"
                  value={field.tooltipMessage ?? ''}
                  onChange={(e) =>
                    onUpdateField({
                      ...field,
                      tooltipMessage: e.target.value || undefined,
                    } as LeafField)
                  }
                  placeholder="Opcjonalna wskazówka dla obywatela"
                  className={inputClass}
                />
              </label>

              {supportsReadOnly && (
                <label className="flex items-center gap-2.5 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={'readOnly' in field ? Boolean(field.readOnly) : false}
                    onChange={(e) =>
                      onUpdateField({
                        ...field,
                        readOnly: e.target.checked ? true : undefined,
                      } as LeafField)
                    }
                    className="h-4 w-4"
                  />
                  <span>Pole tylko do odczytu (readOnly)</span>
                </label>
              )}

              {supportsDefaultValue && (
                <div className="space-y-3 rounded-xl border border-slate-200 bg-slate-50/70 p-3.5">
                  <label className="flex items-center gap-2.5 text-sm font-medium text-slate-800">
                    <input
                      type="checkbox"
                      checked={
                        'defaultValueType' in field &&
                        field.defaultValueType === 'EXTERNAL_SYSTEM'
                      }
                      onChange={(e) => {
                        if (e.target.checked) {
                          onUpdateField({
                            ...field,
                            defaultValueType: 'EXTERNAL_SYSTEM',
                            defaultValue:
                              ('defaultValue' in field && field.defaultValue) || 'MLPZ.name',
                            readOnly: true,
                          } as LeafField);
                        } else {
                          onUpdateField({
                            ...field,
                            defaultValueType: undefined,
                          } as LeafField);
                        }
                      }}
                      className="h-4 w-4"
                    />
                    <span>Zasilane z rejestru (EXTERNAL_SYSTEM)</span>
                  </label>

                  {'defaultValueType' in field && field.defaultValueType === 'EXTERNAL_SYSTEM' ? (
                    <select
                      value={'defaultValue' in field ? (field.defaultValue ?? '') : ''}
                      onChange={(e) =>
                        onUpdateField({ ...field, defaultValue: e.target.value } as LeafField)
                      }
                      className={inputClass}
                    >
                      {INITIAL_DATA_LIST.map((item) => (
                        <option key={item.reference} value={item.reference}>
                          {item.reference} — {item.label}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      value={'defaultValue' in field ? (field.defaultValue ?? '') : ''}
                      onChange={(e) =>
                        onUpdateField({
                          ...field,
                          defaultValue: e.target.value || undefined,
                        } as LeafField)
                      }
                      placeholder="Opcjonalna wartość domyślna"
                      className={inputClass}
                    />
                  )}
                </div>
              )}

              {supportsStates && 'states' in field && (
                <div className="space-y-2.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-700">
                      Warunki dynamiczne JSONata ({field.states?.length ?? 0})
                    </span>
                    <Button variant="ghost" size="xs" onClick={handleAddState}>
                      <Plus className="h-3.5 w-3.5" />
                      Dodaj warunek
                    </Button>
                  </div>
                  {(field.states ?? []).map((st, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      <select
                        value={st.type}
                        disabled={field.type === 'TEXT_BLOCK'}
                        onChange={(e) =>
                          handleStateChange(idx, { type: e.target.value as State['type'] })
                        }
                        className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs"
                      >
                        <option value="SHOW">SHOW</option>
                        {field.type !== 'TEXT_BLOCK' && <option value="READ_ONLY">READ_ONLY</option>}
                      </select>
                      <input
                        type="text"
                        value={st.query}
                        onChange={(e) => handleStateChange(idx, { query: e.target.value })}
                        placeholder={`"Tak" in tabs['Strona' in title]...`}
                        className="flex-1 rounded-lg border border-slate-300 px-2.5 py-1.5 font-mono text-xs"
                      />
                      <button
                        type="button"
                        onClick={() => handleRemoveState(idx)}
                        aria-label="Usuń warunek"
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Stopka szuflady z akcjami */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 bg-slate-50 px-6 py-4">
        <div className="flex items-center gap-2">
          {onFocusOnDocument && assessment?.sources[0]?.bbox && (
            <Button variant="ghost" size="sm" onClick={onFocusOnDocument}>
              <MapPin className="h-4 w-4" />
              Pokaż źródło
            </Button>
          )}
          {isModified && (
            <Button variant="ghost" size="sm" onClick={() => onResetField(field.id)}>
              <RotateCcw className="h-4 w-4" />
              Cofnij
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant={isReviewed ? 'success' : 'secondary'}
            size="sm"
            onClick={() => onToggleReviewed(field.id)}
          >
            <Check className="h-4 w-4" />
            {isReviewed ? 'Sprawdzone' : 'Oznacz sprawdzone'}
          </Button>
          <Button variant="primary" size="sm" onClick={onClose}>
            Gotowe
          </Button>
        </div>
      </div>
    </aside>
  );
}
