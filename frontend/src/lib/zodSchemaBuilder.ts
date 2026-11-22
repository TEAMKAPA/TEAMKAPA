import { z } from 'zod';
import {
  INITIAL_DATA_CATALOG,
  INPUT_FIELD_TYPES,
  MAX_FORM_TITLE_LENGTH,
  TECH_NAME_REGEX,
  isChoiceField,
  type ConsentSectionField,
  type FormDefinition,
  type LeafField,
  type SectionField,
  type State,
  type Validator,
} from '../types/coiForm';
import type { Issue } from '../types/auditReport';
import { extractStateReferences, makeAnswerKey } from './conditionEvaluator';

const PESEL_WEIGHTS = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3] as const;
const NIP_WEIGHTS = [6, 5, 7, 2, 3, 4, 5, 6, 7] as const;
const REGON9_WEIGHTS = [8, 9, 2, 3, 4, 5, 6, 7] as const;
const REGON14_WEIGHTS = [2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8] as const;

const ZIP_CODE_RE = /^\d{2}-\d{3}$/;
const EMAIL_RE = /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/;
const EPUAP_RE = /^\/[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;
const ADE_RE = /^AE:PL-\d{5}-\d{5}-[A-Z]{5}-\d{2}$/;
const ID_CARD_RE = /^[A-Z]{3}\s?\d{6}$/;

export function validatePesel(value: string): boolean {
  const digits = value.trim();
  if (!/^\d{11}$/.test(digits)) return false;

  const yearPart = Number(digits.slice(0, 2));
  const monthCode = Number(digits.slice(2, 4));
  const day = Number(digits.slice(4, 6));

  let century: number;
  let month: number;
  if (monthCode >= 1 && monthCode <= 12) {
    century = 1900;
    month = monthCode;
  } else if (monthCode >= 21 && monthCode <= 32) {
    century = 2000;
    month = monthCode - 20;
  } else if (monthCode >= 41 && monthCode <= 52) {
    century = 2100;
    month = monthCode - 40;
  } else if (monthCode >= 61 && monthCode <= 72) {
    century = 2200;
    month = monthCode - 60;
  } else if (monthCode >= 81 && monthCode <= 92) {
    century = 1800;
    month = monthCode - 80;
  } else {
    return false;
  }

  const fullYear = century + yearPart;
  const dt = new Date(Date.UTC(fullYear, month - 1, day));
  if (
    dt.getUTCFullYear() !== fullYear ||
    dt.getUTCMonth() !== month - 1 ||
    dt.getUTCDate() !== day
  ) {
    return false;
  }

  let total = 0;
  for (let i = 0; i < 10; i += 1) total += Number(digits[i]) * PESEL_WEIGHTS[i];
  return (10 - (total % 10)) % 10 === Number(digits[10]);
}

export function validateNip(value: string): boolean {
  const digits = value.trim().replace(/[\s-]/g, '');
  if (!/^\d{10}$/.test(digits) || digits === '0000000000') return false;
  let sum = 0;
  for (let i = 0; i < 9; i += 1) sum += Number(digits[i]) * NIP_WEIGHTS[i];
  return sum % 11 === Number(digits[9]);
}

function checkRegonChecksum(digits: string, weights: readonly number[]): boolean {
  let sum = 0;
  for (let i = 0; i < weights.length; i += 1) sum += Number(digits[i]) * weights[i];
  const mod = sum % 11;
  return (mod === 10 ? 0 : mod) === Number(digits[digits.length - 1]);
}

export function validateRegon(value: string): boolean {
  const digits = value.trim().replace(/[\s-]/g, '');
  if (!/^\d+$/.test(digits) || /^0+$/.test(digits)) return false;
  if (digits.length === 9) return checkRegonChecksum(digits, REGON9_WEIGHTS);
  if (digits.length === 14) {
    return (
      checkRegonChecksum(digits.slice(0, 9), REGON9_WEIGHTS) &&
      checkRegonChecksum(digits, REGON14_WEIGHTS)
    );
  }
  return false;
}

export function validateZipCode(value: string): boolean {
  return ZIP_CODE_RE.test(value.trim());
}

export function validateEmail(value: string): boolean {
  const text = value.trim();
  if (!text || text.length > 254 || text.includes('..') || !EMAIL_RE.test(text)) return false;
  const [local, domain] = text.split('@');
  if (local.length > 64 || local.startsWith('.') || local.endsWith('.')) return false;
  const labels = domain.split('.');
  if (labels.some((l) => l.startsWith('-') || l.endsWith('-'))) return false;
  const tld = labels[labels.length - 1];
  return tld.length >= 2 && /^[A-Za-z]+$/.test(tld);
}

export function validateEpuap(value: string): boolean {
  return EPUAP_RE.test(value.trim());
}

export function validateAde(value: string): boolean {
  return ADE_RE.test(value.trim().toUpperCase());
}

export function validateIdCardSerial(value: string): boolean {
  return ID_CARD_RE.test(value.trim().toUpperCase());
}

function runFieldValidator(validator: Validator, value: string): string | null {
  switch (validator) {
    case 'REQUIRED':
      return value.trim().length > 0 ? null : 'To pole jest wymagane';
    case 'EMAIL':
      return validateEmail(value) ? null : 'Nieprawidłowy adres e-mail';
    case 'PESEL':
      return validatePesel(value) ? null : 'Nieprawidłowy numer PESEL (11 cyfr i suma kontrolna)';
    case 'NIP':
      return validateNip(value) ? null : 'Nieprawidłowy numer NIP (10 cyfr i suma kontrolna)';
    case 'REGON':
      return validateRegon(value) ? null : 'Nieprawidłowy numer REGON (9 lub 14 cyfr)';
    case 'ZIP_CODE':
      return validateZipCode(value) ? null : 'Wymagany format kodu pocztowego XX-XXX';
    case 'EPUAP':
      return validateEpuap(value) ? null : 'Wymagany format skrytki ePUAP (/podmiot/skrytka)';
    case 'ADE':
      return validateAde(value) ? null : 'Wymagany format adresu ADE';
    case 'SERIAL_NUMBER_ID_CARD':
      return validateIdCardSerial(value) ? null : 'Wymagany format serii i numeru dowodu';
    default:
      return null;
  }
}

export function buildFieldZodSchema(field: LeafField): z.ZodTypeAny {
  if (field.type === 'TEXT_BLOCK' || field.type === 'INFO_SECTION') return z.any().optional();

  if (field.type === 'CONSENT_SECTION') {
    const required = Boolean(
      field.consentSectionConfig.required ||
        field.consentSectionConfig.consents.some((c) => c.required),
    );
    return z.unknown().superRefine((val, ctx) => {
      if (!required) return;
      const accepted =
        val === true ||
        (Array.isArray(val) &&
          field.consentSectionConfig.consents.every(
            (c, idx) => !c.required || val.includes(String(idx)),
          ));
      if (!accepted) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: 'Wymagane wyrażenie obowiązkowych zgód',
        });
      }
    });
  }

  const validators: readonly Validator[] = 'validation' in field ? field.validation : [];
  const isRequired = validators.includes('REQUIRED');

  if (field.type === 'CHECKBOX') {
    return z.unknown().superRefine((val, ctx) => {
      const list = Array.isArray(val) ? val : [];
      if (isRequired && list.length === 0) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'Zaznacz co najmniej jedną opcję' });
      }
    });
  }

  return z.unknown().superRefine((val, ctx) => {
    const str = val === null || val === undefined ? '' : String(val);
    if (!str.trim()) {
      if (isRequired) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'To pole jest wymagane' });
      }
      return;
    }
    if (field.type === 'NUMBER' && Number.isNaN(Number(str.replace(',', '.')))) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'Wprowadź poprawną liczbę' });
      return;
    }
    for (const v of validators) {
      if (v === 'REQUIRED') continue;
      const err = runFieldValidator(v, str);
      if (err) ctx.addIssue({ code: z.ZodIssueCode.custom, message: err });
    }
  });
}

export function buildFormZodSchema(
  form: FormDefinition,
  visibleAnswerKeys?: ReadonlySet<string>,
): z.ZodObject<Record<string, z.ZodTypeAny>> {
  const shape: Record<string, z.ZodTypeAny> = {};
  for (const tab of form.tabs) {
    for (const sq of tab.questions) {
      for (const fq of sq.field.repetingSectionGrid) {
        const key = makeAnswerKey(tab.title, sq.field.techName, fq.field.techName);
        shape[key] =
          visibleAnswerKeys && !visibleAnswerKeys.has(key)
            ? z.any().optional()
            : buildFieldZodSchema(fq.field);
      }
    }
  }
  return z.object(shape);
}

function errIssue(code: string, message: string, path = '', fieldId?: string): Issue {
  return { code, severity: 'ERROR', message, path, field_id: fieldId ?? null };
}

function warnIssue(code: string, message: string, path = '', fieldId?: string): Issue {
  return { code, severity: 'WARNING', message, path, field_id: fieldId ?? null };
}

export function validateFormDefinitionRules(form: FormDefinition): Issue[] {
  const issues: Issue[] = [];

  if (!form.title.trim()) {
    issues.push(errIssue('FORM_TITLE_EMPTY', 'Tytuł formularza nie może być pusty', '/title'));
  } else if (form.title.length > MAX_FORM_TITLE_LENGTH) {
    issues.push(
      errIssue('FORM_TITLE_TOO_LONG', `Tytuł przekracza ${MAX_FORM_TITLE_LENGTH} znaków`, '/title'),
    );
  }

  if (form.tabs.length < 2) {
    issues.push(errIssue('TABS_MIN_TWO', 'Formularz musi mieć co najmniej dwie strony', '/tabs'));
  }
  if (form.tabs.length > 0 && form.tabs[form.tabs.length - 1].type !== 'FINISH') {
    issues.push(
      errIssue(
        'FINISH_TAB_LAST',
        'Ostatnia strona musi być typu FINISH',
        `/tabs/${form.tabs.length - 1}/type`,
      ),
    );
  }

  const finishCount = form.tabs.filter((t) => t.type === 'FINISH').length;
  if (finishCount > 1) {
    issues.push(
      errIssue('FINISH_TAB_SINGLE', `Tylko jedna strona może być FINISH (jest ${finishCount})`, '/tabs'),
    );
  }

  const tabTitleSet = new Set(form.tabs.map((t) => t.title));
  const sectionKeys = new Set<string>();
  const elementIndex = new Map<
    string,
    { path: string; tabIndex: number; fieldId: string; states: State[]; leaf?: LeafField }
  >();

  form.tabs.forEach((tab, t) => {
    const leavesCount = tab.questions.reduce((s, q) => s + q.field.repetingSectionGrid.length, 0);
    if (leavesCount === 0) {
      issues.push(
        errIssue('TAB_EMPTY', `Strona „${tab.title}” musi zawierać co najmniej jedno pole`, `/tabs/${t}`),
      );
    }

    tab.questions.forEach((sq, s) => {
      const section = sq.field;
      const sPath = `/tabs/${t}/questions/${s}/field`;
      if (!TECH_NAME_REGEX.test(section.techName)) {
        issues.push(
          errIssue('TECHNAME_INVALID', `Nieprawidłowa nazwa techniczna sekcji „${section.techName}”`, sPath, section.id),
        );
      }
      const secKey = `${tab.title}::${section.techName}`;
      sectionKeys.add(secKey);

      const fieldTechCounts = new Map<string, number>();
      for (const fq of section.repetingSectionGrid) {
        fieldTechCounts.set(fq.field.techName, (fieldTechCounts.get(fq.field.techName) ?? 0) + 1);
      }

      section.repetingSectionGrid.forEach((fq, f) => {
        const leaf = fq.field;
        const fPath = `${sPath}/repetingSectionGrid/${f}/field`;

        if (!TECH_NAME_REGEX.test(leaf.techName)) {
          issues.push(
            errIssue('TECHNAME_INVALID', `Nieprawidłowa nazwa techniczna pola „${leaf.techName}”`, `${fPath}/techName`, leaf.id),
          );
        }
        if ((fieldTechCounts.get(leaf.techName) ?? 0) > 1) {
          issues.push(
            errIssue('TECHNAME_DUPLICATE_IN_SECTION', `techName „${leaf.techName}” powtarza się w sekcji`, fPath, leaf.id),
          );
        }
        if (tab.type === 'FINISH' && leaf.type !== 'INFO_SECTION') {
          issues.push(
            errIssue('FINISH_ONLY_INFO_SECTION', `Na stronie FINISH dozwolone jest tylko INFO_SECTION`, fPath, leaf.id),
          );
        }
        if (INPUT_FIELD_TYPES.has(leaf.type) && !('title' in leaf && leaf.title?.trim())) {
          issues.push(warnIssue('INPUT_WITHOUT_TITLE', 'Pole nie ma etykiety', fPath, leaf.id));
        }
        if (isChoiceField(leaf)) {
          if (leaf.dictionaryName && leaf.options.length > 0) {
            issues.push(errIssue('CHOICE_DICT_WITH_OPTIONS', 'Pole ze słownikiem musi mieć puste options', fPath, leaf.id));
          } else if (!leaf.dictionaryName && leaf.options.length === 0) {
            issues.push(errIssue('CHOICE_EMPTY_OPTIONS', 'Pole wyboru wymaga opcji lub słownika', fPath, leaf.id));
          }
        }
        if (leaf.type === 'INFO_SECTION' && !leaf.description.trim()) {
          issues.push(errIssue('INFO_DESCRIPTION_REQUIRED', 'INFO_SECTION wymaga opisu', fPath, leaf.id));
        }

        const defaultValue = 'defaultValue' in leaf ? leaf.defaultValue : undefined;
        const defaultValueType = 'defaultValueType' in leaf ? leaf.defaultValueType : undefined;
        const readOnly = 'readOnly' in leaf ? leaf.readOnly : undefined;
        if (defaultValueType === 'EXTERNAL_SYSTEM') {
          if (!defaultValue || !INITIAL_DATA_CATALOG.has(defaultValue)) {
            issues.push(errIssue('INITIAL_DATA_UNKNOWN', `Nieznane dane inicjalne „${defaultValue ?? ''}”`, `${fPath}/defaultValue`, leaf.id));
          }
          if (readOnly !== true) {
            issues.push(errIssue('INITIAL_DATA_NOT_READONLY', 'Dane inicjalne wymagają readOnly: true', `${fPath}/readOnly`, leaf.id));
          }
        }

        elementIndex.set(`${secKey}::${leaf.techName}`, {
          path: fPath,
          tabIndex: t,
          fieldId: leaf.id,
          states: 'states' in leaf && leaf.states ? leaf.states : [],
          leaf,
        });
      });
    });
  });

  for (const elem of elementIndex.values()) {
    elem.states.forEach((state, idx) => {
      const refs = extractStateReferences(state.query);
      if (refs.length === 0) {
        issues.push(warnIssue('STATE_QUERY_UNPARSED', 'Nierozpoznany warunek JSONata', `${elem.path}/states/${idx}/query`, elem.fieldId));
      }
      for (const ref of refs) {
        if (!tabTitleSet.has(ref.tab)) {
          issues.push(errIssue('STATE_UNKNOWN_TAB', `Nieistniejąca strona „${ref.tab}” w warunku`, elem.path, elem.fieldId));
        } else if (!sectionKeys.has(`${ref.tab}::${ref.section}`)) {
          issues.push(errIssue('STATE_UNKNOWN_SECTION', `Nieistniejąca sekcja „${ref.section}” w warunku`, elem.path, elem.fieldId));
        }
      }
    });
  }

  return issues;
}

function stripEmpty<T extends object>(obj: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v !== undefined && v !== null) out[k] = v;
  }
  return out as T;
}

function sanitizeLeafField(field: LeafField): LeafField {
  const base = stripEmpty({
    id: field.id,
    techName: field.techName.trim(),
    questionOrder: field.questionOrder,
    tooltipMessage: field.tooltipMessage?.trim() || undefined,
  });

  const cleanStates = (states?: State[]): State[] | undefined => {
    if (!states || states.length === 0) return undefined;
    const valid = states
      .filter((s) => s.query.trim().length > 0)
      .map((s) => ({ type: s.type, query: s.query.trim() }));
    return valid.length > 0 ? valid : undefined;
  };

  switch (field.type) {
    case 'TEXT_FIELD':
      return stripEmpty({
        ...base,
        type: 'TEXT_FIELD' as const,
        title: field.title?.trim() || undefined,
        description: field.description?.trim() || undefined,
        readOnly: field.readOnly,
        defaultValue: field.defaultValue?.trim() || undefined,
        defaultValueType: field.defaultValue?.trim() ? field.defaultValueType : undefined,
        validation: [...field.validation],
        states: cleanStates(field.states),
      });
    case 'CHECKBOX':
      return stripEmpty({
        ...base,
        type: 'CHECKBOX' as const,
        title: field.title?.trim() || undefined,
        description: field.description?.trim() || undefined,
        readOnly: field.readOnly,
        dictionaryName: field.dictionaryName?.trim() || undefined,
        options: field.dictionaryName?.trim()
          ? []
          : field.options.map((o) => ({ id: o.id, name: o.name.trim(), techName: o.techName.trim() })),
        validation: field.validation.filter((v): v is 'REQUIRED' => v === 'REQUIRED'),
        states: cleanStates(field.states),
      });
    case 'NUMBER':
      return stripEmpty({
        ...base,
        type: 'NUMBER' as const,
        title: field.title?.trim() || undefined,
        description: field.description?.trim() || undefined,
        readOnly: field.readOnly,
        validation: field.validation.filter((v): v is 'REQUIRED' => v === 'REQUIRED'),
        states: cleanStates(field.states),
      });
    case 'SELECT':
      return stripEmpty({
        ...base,
        type: 'SELECT' as const,
        title: field.title?.trim() || undefined,
        description: field.description?.trim() || undefined,
        readOnly: field.readOnly,
        searchEnabled: field.searchEnabled,
        dictionaryName: field.dictionaryName?.trim() || undefined,
        options: field.dictionaryName?.trim()
          ? []
          : field.options.map((o) => ({ id: o.id, name: o.name.trim(), techName: o.techName.trim() })),
        defaultValue: field.defaultValue?.trim() || undefined,
        defaultValueType: field.defaultValue?.trim() ? field.defaultValueType : undefined,
        validation: field.validation.filter((v): v is 'REQUIRED' => v === 'REQUIRED'),
        states: cleanStates(field.states),
      });
    case 'TEXTAREA':
      return stripEmpty({
        ...base,
        type: 'TEXTAREA' as const,
        title: field.title?.trim() || undefined,
        description: field.description?.trim() || undefined,
        readOnly: field.readOnly,
        defaultValue: field.defaultValue?.trim() || undefined,
        defaultValueType: field.defaultValue?.trim() ? field.defaultValueType : undefined,
        validation: field.validation.filter((v): v is 'REQUIRED' => v === 'REQUIRED'),
        states: cleanStates(field.states),
      });
    case 'FILE_UPLOADER':
      return stripEmpty({
        ...base,
        type: 'FILE_UPLOADER' as const,
        title: field.title?.trim() || undefined,
        description: field.description?.trim() || undefined,
        defaultValue: field.defaultValue?.trim() || undefined,
        validation: field.validation.filter((v): v is 'REQUIRED' => v === 'REQUIRED'),
        states: cleanStates(field.states),
      });
    case 'RADIO':
      return stripEmpty({
        ...base,
        type: 'RADIO' as const,
        title: field.title?.trim() || undefined,
        description: field.description?.trim() || undefined,
        readOnly: field.readOnly,
        dictionaryName: field.dictionaryName?.trim() || undefined,
        options: field.dictionaryName?.trim()
          ? []
          : field.options.map((o) => ({ id: o.id, name: o.name.trim(), techName: o.techName.trim() })),
        validation: field.validation.filter((v): v is 'REQUIRED' => v === 'REQUIRED'),
        states: cleanStates(field.states),
      });
    case 'DATEPICKER':
      return stripEmpty({
        ...base,
        type: 'DATEPICKER' as const,
        title: field.title?.trim() || undefined,
        description: field.description?.trim() || undefined,
        readOnly: field.readOnly,
        defaultValue: field.defaultValue?.trim() || undefined,
        defaultValueType: field.defaultValue?.trim() ? field.defaultValueType : undefined,
        validation: field.validation.filter((v): v is 'REQUIRED' => v === 'REQUIRED'),
        states: cleanStates(field.states),
      });
    case 'TEXT_BLOCK': {
      const showOnly = field.states
        ?.filter((s) => s.type === 'SHOW' && s.query.trim().length > 0)
        .map((s) => ({ type: 'SHOW' as const, query: s.query.trim() }));
      return stripEmpty({
        ...base,
        type: 'TEXT_BLOCK' as const,
        title: field.title?.trim() || undefined,
        description: field.description?.trim() || undefined,
        states: showOnly && showOnly.length > 0 ? showOnly : undefined,
      });
    }
    case 'CONSENT_SECTION': {
      const consent: ConsentSectionField = stripEmpty({
        ...base,
        type: 'CONSENT_SECTION' as const,
        selectAllEnabled: field.selectAllEnabled,
        consentSectionConfig: stripEmpty({
          consents: field.consentSectionConfig.consents.map((c) =>
            stripEmpty({ description: c.description.trim(), required: c.required }),
          ),
          required: field.consentSectionConfig.required,
          files: [...(field.consentSectionConfig.files ?? [])],
        }),
      });
      return consent;
    }
    case 'INFO_SECTION':
      return stripEmpty({
        ...base,
        type: 'INFO_SECTION' as const,
        description: field.description.trim(),
      });
  }
}

function sanitizeSection(section: SectionField): SectionField {
  const showOnly = section.states
    ?.filter((s) => s.type === 'SHOW' && s.query.trim().length > 0)
    .map((s) => ({ type: 'SHOW' as const, query: s.query.trim() }));

  return stripEmpty({
    id: section.id,
    techName: section.techName.trim(),
    questionOrder: section.questionOrder,
    tooltipMessage: section.tooltipMessage?.trim() || undefined,
    type: section.type,
    title: section.title.trim(),
    states: showOnly && showOnly.length > 0 ? showOnly : undefined,
    repetingSectionGrid: section.repetingSectionGrid.map((fq) => ({
      x: fq.x,
      y: fq.y,
      rows: fq.rows,
      cols: fq.cols,
      field: sanitizeLeafField(fq.field),
    })),
  });
}

export function sanitizeFormForExport(form: FormDefinition): FormDefinition {
  return {
    title: form.title.trim(),
    tabs: form.tabs.map((tab) => ({
      id: tab.id,
      title: tab.title.trim(),
      type: tab.type,
      questions: tab.questions.map((sq) => ({
        x: sq.x,
        y: sq.y,
        rows: sq.rows,
        cols: sq.cols,
        field: sanitizeSection(sq.field),
      })),
    })),
    flow: {
      links: form.flow.links.map((link) => ({
        id: link.id,
        source: link.source,
        target: link.target,
      })),
    },
  };
}
