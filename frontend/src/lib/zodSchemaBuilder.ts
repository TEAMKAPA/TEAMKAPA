/**
 * 1. Dynamiczny builder schematów Zod z polskimi walidatorami domenowymi
 *    (1:1 z backend/shared/validators/domain.py) do interaktywnego podglądu wypełniania.
 * 2. Kliencka walidacja reguł COI (1:1 z backend/shared/validators/form_rules.py)
 *    przed zatwierdzeniem formularza.
 * 3. Sanityzacja `FormDefinition` do czystego eksportu COI (bez niedozwolonych kluczy / null).
 */

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

// ---------------------------------------------------------------- polskie walidatory domenowe

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
  for (let i = 0; i < 10; i += 1) {
    total += Number(digits[i]) * PESEL_WEIGHTS[i];
  }
  const control = (10 - (total % 10)) % 10;
  return control === Number(digits[10]);
}

export function validateNip(value: string): boolean {
  const digits = value.trim().replace(/[\s-]/g, '');
  if (!/^\d{10}$/.test(digits) || digits === '0000000000') return false;

  let sum = 0;
  for (let i = 0; i < 9; i += 1) {
    sum += Number(digits[i]) * NIP_WEIGHTS[i];
  }
  return sum % 11 === Number(digits[9]);
}

function checkRegonChecksum(digits: string, weights: readonly number[]): boolean {
  let sum = 0;
  for (let i = 0; i < weights.length; i += 1) {
    sum += Number(digits[i]) * weights[i];
  }
  const mod = sum % 11;
  const control = mod === 10 ? 0 : mod;
  return control === Number(digits[digits.length - 1]);
}

export function validateRegon(value: string): boolean {
  const digits = value.trim().replace(/[\s-]/g, '');
  if (!/^\d+$/.test(digits) || /^0+$/.test(digits)) return false;
  if (digits.length === 9) {
    return checkRegonChecksum(digits, REGON9_WEIGHTS);
  }
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
  if (!text || text.length > 254 || text.includes('..') || !EMAIL_RE.test(text)) {
    return false;
  }
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
      return validateAde(value) ? null : 'Wymagany format adresu ADE (AE:PL-XXXXX-XXXXX-XXXXX-XX)';
    case 'SERIAL_NUMBER_ID_CARD':
      return validateIdCardSerial(value) ? null : 'Wymagany format serii i numeru dowodu (ABC123456)';
    default:
      return null;
  }
}

// ---------------------------------------------------------------- builder Zod do podglądu wypełniania

export function buildFieldZodSchema(field: LeafField): z.ZodTypeAny {
  if (field.type === 'TEXT_BLOCK' || field.type === 'INFO_SECTION') {
    return z.any().optional();
  }

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
          field.consentSectionConfig.consents.every((c, idx) => !c.required || val.includes(String(idx))));
      if (!accepted) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: 'Wymagane wyrażenie obowiązkowych zgód',
        });
      }
    });
  }

  const validators: Validator[] = 'validation' in field ? (field.validation as Validator[]) : [];
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
      if (err) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, message: err });
      }
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
      const section = sq.field;
      for (const fq of section.repetingSectionGrid) {
        const key = makeAnswerKey(tab.title, section.techName, fq.field.techName);
        if (visibleAnswerKeys && !visibleAnswerKeys.has(key)) {
          shape[key] = z.any().optional();
          continue;
        }
        shape[key] = buildFieldZodSchema(fq.field);
      }
    }
  }

  return z.object(shape);
}

// ---------------------------------------------------------------- reguły techniczne COI (port form_rules.py)

function errIssue(code: string, message: string, path = '', fieldId?: string): Issue {
  return { code, severity: 'ERROR', message, path, field_id: fieldId ?? null };
}

function warnIssue(code: string, message: string, path = '', fieldId?: string): Issue {
  return { code, severity: 'WARNING', message, path, field_id: fieldId ?? null };
}

function sectionPath(t: number, s: number): string {
  return `/tabs/${t}/questions/${s}/field`;
}

function fieldPath(t: number, s: number, f: number): string {
  return `${sectionPath(t, s)}/repetingSectionGrid/${f}/field`;
}

export function validateFormDefinitionRules(form: FormDefinition): Issue[] {
  const issues: Issue[] = [];

  if (!form.title.trim()) {
    issues.push(errIssue('FORM_TITLE_EMPTY', 'Tytuł formularza nie może być pusty', '/title'));
  } else if (form.title.length > MAX_FORM_TITLE_LENGTH) {
    issues.push(
      errIssue(
        'FORM_TITLE_TOO_LONG',
        `Tytuł formularza przekracza ${MAX_FORM_TITLE_LENGTH} znaków`,
        '/title',
      ),
    );
  }

  // 1. Strony (tabs)
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
  const finishIndices = form.tabs
    .map((t, i) => (t.type === 'FINISH' ? i : -1))
    .filter((i) => i >= 0);
  if (finishIndices.length > 1) {
    issues.push(
      errIssue(
        'FINISH_TAB_SINGLE',
        `Tylko jedna strona może być FINISH, jest ${finishIndices.length}; pozostałe muszą być MAIN`,
        '/tabs',
      ),
    );
  }

  const tabTitleCounts = new Map<string, number>();
  for (const tab of form.tabs) {
    tabTitleCounts.set(tab.title, (tabTitleCounts.get(tab.title) ?? 0) + 1);
  }
  for (const [title, count] of tabTitleCounts) {
    if (count > 1) {
      issues.push(
        errIssue(
          'TAB_TITLE_DUPLICATE',
          `Tytuł strony „${title}” powtarza się (JSONata adresuje strony po tytule)`,
          '/tabs',
        ),
      );
    }
  }

  // 2. ID i struktura stron / sekcji / pól
  const idOccurrences = new Map<string, string[]>();
  const recordId = (id: string, path: string) => {
    const list = idOccurrences.get(id) ?? [];
    list.push(path);
    idOccurrences.set(id, list);
  };

  const globalTechCounts = new Map<string, number>();
  type ElementKey = string;
  interface IndexedElement {
    key: ElementKey;
    tabTitle: string;
    sectionTech: string;
    fieldTech: string | null;
    path: string;
    tabIndex: number;
    fieldId: string;
    states: State[];
    leaf?: LeafField;
    dependsOn: Set<ElementKey>;
  }
  const elementIndex = new Map<ElementKey, IndexedElement>();
  const sectionKeys = new Set<string>();

  form.tabs.forEach((tab, t) => {
    recordId(tab.id, `/tabs/${t}/id`);
    const leavesCount = tab.questions.reduce(
      (sum, q) => sum + q.field.repetingSectionGrid.length,
      0,
    );
    if (leavesCount === 0) {
      issues.push(
        errIssue('TAB_EMPTY', `Strona „${tab.title}” musi zawierać co najmniej jedno pole`, `/tabs/${t}`),
      );
    }

    const sectionTechCounts = new Map<string, number>();
    for (const q of tab.questions) {
      sectionTechCounts.set(q.field.techName, (sectionTechCounts.get(q.field.techName) ?? 0) + 1);
    }

    tab.questions.forEach((sq, s) => {
      const section = sq.field;
      const sPath = sectionPath(t, s);
      recordId(section.id, `${sPath}/id`);
      globalTechCounts.set(section.techName, (globalTechCounts.get(section.techName) ?? 0) + 1);

      if (!TECH_NAME_REGEX.test(section.techName)) {
        issues.push(
          errIssue(
            'TECHNAME_INVALID',
            `Nieprawidłowa nazwa techniczna sekcji „${section.techName}” (dozwolone litery, cyfry i _)`,
            `${sPath}/techName`,
            section.id,
          ),
        );
      }

      if ((sectionTechCounts.get(section.techName) ?? 0) > 1) {
        issues.push(
          errIssue(
            'TECHNAME_DUPLICATE_IN_TAB',
            `techName sekcji „${section.techName}” powtarza się na stronie „${tab.title}”`,
            sPath,
            section.id,
          ),
        );
      }

      if (section.repetingSectionGrid.length === 0) {
        issues.push(
          warnIssue('SECTION_EMPTY', `Sekcja „${section.title}” nie zawiera pól`, sPath, section.id),
        );
      }

      const secKey = `${tab.title}::${section.techName}`;
      sectionKeys.add(secKey);
      const secElemKey = `${secKey}::__section__`;
      if (!elementIndex.has(secElemKey)) {
        elementIndex.set(secElemKey, {
          key: secElemKey,
          tabTitle: tab.title,
          sectionTech: section.techName,
          fieldTech: null,
          path: sPath,
          tabIndex: t,
          fieldId: section.id,
          states: section.states ?? [],
          dependsOn: new Set(),
        });
      }

      const fieldTechCounts = new Map<string, number>();
      for (const fq of section.repetingSectionGrid) {
        fieldTechCounts.set(fq.field.techName, (fieldTechCounts.get(fq.field.techName) ?? 0) + 1);
      }

      section.repetingSectionGrid.forEach((fq, f) => {
        const leaf = fq.field;
        const fPath = fieldPath(t, s, f);
        recordId(leaf.id, `${fPath}/id`);
        globalTechCounts.set(leaf.techName, (globalTechCounts.get(leaf.techName) ?? 0) + 1);

        if (!TECH_NAME_REGEX.test(leaf.techName)) {
          issues.push(
            errIssue(
              'TECHNAME_INVALID',
              `Nieprawidłowa nazwa techniczna pola „${leaf.techName}” (dozwolone litery, cyfry i _)`,
              `${fPath}/techName`,
              leaf.id,
            ),
          );
        }

        if ((fieldTechCounts.get(leaf.techName) ?? 0) > 1) {
          issues.push(
            errIssue(
              'TECHNAME_DUPLICATE_IN_SECTION',
              `techName pola „${leaf.techName}” powtarza się w sekcji „${section.techName}”`,
              fPath,
              leaf.id,
            ),
          );
        }

        if (tab.type === 'FINISH' && leaf.type !== 'INFO_SECTION') {
          issues.push(
            errIssue(
              'FINISH_ONLY_INFO_SECTION',
              `Na stronie FINISH dozwolone są wyłącznie pola INFO_SECTION, a „${leaf.techName}” ma typ ${leaf.type}`,
              fPath,
              leaf.id,
            ),
          );
        }

        if (INPUT_FIELD_TYPES.has(leaf.type) && !('title' in leaf && leaf.title?.trim())) {
          issues.push(
            warnIssue('INPUT_WITHOUT_TITLE', 'Pole do wypełnienia nie ma etykiety', fPath, leaf.id),
          );
        }

        if (isChoiceField(leaf)) {
          if (leaf.dictionaryName && leaf.options.length > 0) {
            issues.push(
              errIssue(
                'CHOICE_DICT_WITH_OPTIONS',
                'Pole korzystające ze słownika musi mieć pustą tablicę options',
                fPath,
                leaf.id,
              ),
            );
          } else if (!leaf.dictionaryName && leaf.options.length === 0) {
            issues.push(
              errIssue(
                'CHOICE_EMPTY_OPTIONS',
                'Pole wyboru wymaga opcji albo słownika (dictionaryName)',
                fPath,
                leaf.id,
              ),
            );
          }

          const optNames = new Map<string, number>();
          const optTechs = new Map<string, number>();
          const optIds = new Map<string, number>();
          for (const opt of leaf.options) {
            optNames.set(opt.name, (optNames.get(opt.name) ?? 0) + 1);
            optTechs.set(opt.techName, (optTechs.get(opt.techName) ?? 0) + 1);
            optIds.set(opt.id, (optIds.get(opt.id) ?? 0) + 1);
            if (!TECH_NAME_REGEX.test(opt.techName)) {
              issues.push(
                errIssue(
                  'OPTION_TECHNAME_INVALID',
                  `Nieprawidłowa nazwa techniczna opcji „${opt.techName}”`,
                  `${fPath}/options`,
                  leaf.id,
                ),
              );
            }
          }
          for (const [val, n] of optNames) {
            if (n > 1) {
              issues.push(
                errIssue('OPTION_NAME_DUPLICATE', `Opcja name="${val}" powtarza się`, `${fPath}/options`, leaf.id),
              );
            }
          }
          for (const [val, n] of optTechs) {
            if (n > 1) {
              issues.push(
                errIssue('OPTION_TECHNAME_DUPLICATE', `Opcja techName="${val}" powtarza się`, `${fPath}/options`, leaf.id),
              );
            }
          }
          for (const [val, n] of optIds) {
            if (n > 1) {
              issues.push(
                errIssue('OPTION_ID_DUPLICATE', `Opcja id="${val}" powtarza się`, `${fPath}/options`, leaf.id),
              );
            }
          }
        }

        if (leaf.type === 'CONSENT_SECTION') {
          if (leaf.consentSectionConfig.consents.length === 0) {
            issues.push(
              errIssue(
                'CONSENT_EMPTY',
                'Sekcja zgód musi zawierać co najmniej jedną zgodę',
                `${fPath}/consentSectionConfig`,
                leaf.id,
              ),
            );
          }
        }

        if (leaf.type === 'INFO_SECTION' && !leaf.description.trim()) {
          issues.push(
            errIssue(
              'INFO_DESCRIPTION_REQUIRED',
              'Pole INFO_SECTION wymaga niepustego opisu',
              `${fPath}/description`,
              leaf.id,
            ),
          );
        }

        // Dane inicjalne
        const defaultValue = 'defaultValue' in leaf ? leaf.defaultValue : undefined;
        const defaultValueType = 'defaultValueType' in leaf ? leaf.defaultValueType : undefined;
        const readOnly = 'readOnly' in leaf ? leaf.readOnly : undefined;

        if (defaultValueType === 'EXTERNAL_SYSTEM') {
          if (!defaultValue) {
            issues.push(
              errIssue(
                'INITIAL_DATA_MISSING_VALUE',
                'defaultValueType EXTERNAL_SYSTEM wymaga wskazania ŹRÓDŁO.atrybut w defaultValue',
                `${fPath}/defaultValue`,
                leaf.id,
              ),
            );
          } else {
            const entry = INITIAL_DATA_CATALOG.get(defaultValue);
            if (!entry) {
              issues.push(
                errIssue(
                  'INITIAL_DATA_UNKNOWN',
                  `Nieznane źródło/atrybut danych inicjalnych „${defaultValue}”`,
                  `${fPath}/defaultValue`,
                  leaf.id,
                ),
              );
            } else if (entry.fieldType && entry.fieldType !== leaf.type) {
              issues.push(
                warnIssue(
                  'INITIAL_DATA_TYPE_MISMATCH',
                  `„${entry.reference}” powinno zasilać pole ${entry.fieldType}, a pole ma typ ${leaf.type}`,
                  fPath,
                  leaf.id,
                ),
              );
            }
          }
          if (readOnly !== true) {
            issues.push(
              errIssue(
                'INITIAL_DATA_NOT_READONLY',
                'Pole zasilane danymi inicjalnymi musi mieć readOnly: true',
                `${fPath}/readOnly`,
                leaf.id,
              ),
            );
          }
        } else if (defaultValue && INITIAL_DATA_CATALOG.has(defaultValue)) {
          issues.push(
            warnIssue(
              'INITIAL_DATA_MISSING_TYPE',
              `defaultValue „${defaultValue}” wygląda na dane inicjalne, ale brak defaultValueType EXTERNAL_SYSTEM`,
              fPath,
              leaf.id,
            ),
          );
        }

        const fElemKey = `${secKey}::${leaf.techName}`;
        if (!elementIndex.has(fElemKey)) {
          elementIndex.set(fElemKey, {
            key: fElemKey,
            tabTitle: tab.title,
            sectionTech: section.techName,
            fieldTech: leaf.techName,
            path: fPath,
            tabIndex: t,
            fieldId: leaf.id,
            states: 'states' in leaf && leaf.states ? leaf.states : [],
            leaf,
            dependsOn: new Set(),
          });
        }
      });
    });
  });

  form.flow.links.forEach((link, i) => recordId(link.id, `/flow/links/${i}/id`));
  for (const [uid, paths] of idOccurrences) {
    if (paths.length > 1) {
      for (const path of paths) {
        issues.push(errIssue('DUPLICATE_ID', `Identyfikator ${uid} występuje ${paths.length} razy`, path));
      }
    }
  }

  for (const [name, count] of globalTechCounts) {
    if (count > 1) {
      issues.push(warnIssue('TECHNAME_DUPLICATE', `techName „${name}” występuje ${count} razy w formularzu`));
    }
  }

  // 3. Przepływ stron (flow)
  const tabIds = new Set(form.tabs.map((t) => t.id));
  const graph = new Map<string, Set<string>>();
  const reverse = new Map<string, Set<string>>();
  const seenPairs = new Set<string>();

  form.flow.links.forEach((link, i) => {
    const path = `/flow/links/${i}`;
    if (!tabIds.has(link.source) || !tabIds.has(link.target)) {
      issues.push(errIssue('FLOW_UNKNOWN_TAB', 'Przejście wskazuje nieistniejącą stronę', path));
      return;
    }
    if (link.source === link.target) {
      issues.push(errIssue('FLOW_SELF_LINK', 'Przejście ze strony do niej samej', path));
      return;
    }
    const pairKey = `${link.source}->${link.target}`;
    if (seenPairs.has(pairKey)) {
      issues.push(warnIssue('FLOW_DUPLICATE_LINK', 'Powtórzone przejście', path));
    }
    seenPairs.add(pairKey);
    if (!graph.has(link.source)) graph.set(link.source, new Set());
    graph.get(link.source)!.add(link.target);
    if (!reverse.has(link.target)) reverse.set(link.target, new Set());
    reverse.get(link.target)!.add(link.source);
  });

  if (form.tabs.length >= 2) {
    const walk = (start: string, adj: Map<string, Set<string>>): Set<string> => {
      const seen = new Set<string>([start]);
      const stack = [start];
      while (stack.length > 0) {
        const cur = stack.pop()!;
        for (const nxt of adj.get(cur) ?? []) {
          if (!seen.has(nxt)) {
            seen.add(nxt);
            stack.push(nxt);
          }
        }
      }
      return seen;
    };

    const reachable = walk(form.tabs[0].id, graph);
    const canFinish = new Set<string>();
    for (const tab of form.tabs) {
      if (tab.type === 'FINISH') {
        for (const id of walk(tab.id, reverse)) {
          canFinish.add(id);
        }
      }
    }

    form.tabs.forEach((tab, t) => {
      if (!reachable.has(tab.id)) {
        issues.push(
          errIssue(
            'FLOW_UNREACHABLE_TAB',
            `Strona „${tab.title}” jest nieosiągalna z pierwszej strony`,
            `/tabs/${t}`,
          ),
        );
      }
      if (tab.type === 'FINISH' && (graph.get(tab.id)?.size ?? 0) > 0) {
        issues.push(
          errIssue('FLOW_FINISH_OUTGOING', 'Strona FINISH nie może prowadzić dalej', `/tabs/${t}`),
        );
      }
      if (tab.type === 'MAIN' && !canFinish.has(tab.id)) {
        issues.push(
          errIssue(
            'FLOW_NO_PATH_TO_FINISH',
            `Ze strony „${tab.title}” nie da się dojść do strony FINISH`,
            `/tabs/${t}`,
          ),
        );
      }
    });
  }

  // 4. Stany (JSONata)
  const tabTitleSet = new Set(form.tabs.map((t) => t.title));
  for (const elem of elementIndex.values()) {
    elem.states.forEach((state, idx) => {
      const sPath = `${elem.path}/states/${idx}/query`;
      const refs = extractStateReferences(state.query);
      if (refs.length === 0) {
        issues.push(
          warnIssue(
            'STATE_QUERY_UNPARSED',
            'Nie rozpoznano odwołania do pola w warunku JSONata — sprawdź ręcznie',
            sPath,
            elem.fieldId,
          ),
        );
        return;
      }

      for (const ref of refs) {
        if (!tabTitleSet.has(ref.tab)) {
          issues.push(
            errIssue(
              'STATE_UNKNOWN_TAB',
              `Warunek odwołuje się do nieistniejącej strony „${ref.tab}”`,
              sPath,
              elem.fieldId,
            ),
          );
          continue;
        }
        const refSecKey = `${ref.tab}::${ref.section}`;
        if (!sectionKeys.has(refSecKey)) {
          issues.push(
            errIssue(
              'STATE_UNKNOWN_SECTION',
              `Warunek odwołuje się do nieistniejącej sekcji „${ref.section}” na stronie „${ref.tab}”`,
              sPath,
              elem.fieldId,
            ),
          );
          continue;
        }
        const targetKey = `${refSecKey}::${ref.field}`;
        const target = elementIndex.get(targetKey);
        if (!target) {
          issues.push(
            errIssue(
              'STATE_UNKNOWN_FIELD',
              `Warunek odwołuje się do nieistniejącego pola „${ref.field}” w sekcji „${ref.section}”`,
              sPath,
              elem.fieldId,
            ),
          );
          continue;
        }
        if (
          targetKey === elem.key ||
          (elem.fieldTech === null &&
            target.tabTitle === elem.tabTitle &&
            target.sectionTech === elem.sectionTech)
        ) {
          issues.push(
            errIssue(
              'STATE_SELF_REFERENCE',
              'Element nie może zależeć od samego siebie ani od pola we własnej sekcji',
              sPath,
              elem.fieldId,
            ),
          );
          continue;
        }
        if (target.tabIndex > elem.tabIndex) {
          issues.push(
            warnIssue(
              'STATE_FORWARD_REFERENCE',
              'Warunek zależy od pola z późniejszej strony',
              sPath,
              elem.fieldId,
            ),
          );
        }
        if (
          ref.value !== null &&
          target.leaf &&
          isChoiceField(target.leaf) &&
          !target.leaf.dictionaryName
        ) {
          const optNames = new Set(target.leaf.options.map((o) => o.name));
          if (!optNames.has(ref.value)) {
            issues.push(
              errIssue(
                'STATE_UNKNOWN_OPTION',
                `Wartość „${ref.value}” nie jest opcją pola „${target.leaf.techName}”`,
                sPath,
                elem.fieldId,
              ),
            );
          }
        }
        elem.dependsOn.add(targetKey);
      }
    });
  }

  // Wykrywanie cykli zależności
  const color = new Map<string, 0 | 1 | 2>();
  for (const key of elementIndex.keys()) color.set(key, 0);

  const visit = (key: string, trail: string[]) => {
    color.set(key, 1);
    const elem = elementIndex.get(key);
    if (!elem) return;
    for (const nxt of elem.dependsOn) {
      const nxtColor = color.get(nxt);
      if (nxtColor === 1) {
        const startIdx = trail.indexOf(nxt);
        const cycle = startIdx >= 0 ? [...trail.slice(startIdx), nxt] : [key, nxt];
        const names = cycle
          .map((k) => {
            const parts = k.split('::');
            return parts[2] === '__section__' ? parts[1] : parts[2];
          })
          .join(' → ');
        issues.push(
          errIssue('STATE_CYCLE', `Cykl zależności warunków: ${names}`, elem.path, elem.fieldId),
        );
      } else if (nxtColor === 0) {
        visit(nxt, [...trail, nxt]);
      }
    }
    color.set(key, 2);
  };

  for (const key of elementIndex.keys()) {
    if (color.get(key) === 0) {
      visit(key, [key]);
    }
  }

  return issues;
}

// ---------------------------------------------------------------- sanityzacja do czystego JSON COI

function stripEmpty<T extends object>(obj: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v !== undefined && v !== null) {
      out[k] = v;
    }
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
          : field.options.map((o) => ({
              id: o.id,
              name: o.name.trim(),
              techName: o.techName.trim(),
            })),
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
          : field.options.map((o) => ({
              id: o.id,
              name: o.name.trim(),
              techName: o.techName.trim(),
            })),
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
          : field.options.map((o) => ({
              id: o.id,
              name: o.name.trim(),
              techName: o.techName.trim(),
            })),
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
            stripEmpty({
              description: c.description.trim(),
              required: c.required,
            }),
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
