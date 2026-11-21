/**
 * Parser i ewaluator warunków dynamicznych JSONata z formularzy COI (`SHOW` i `READ_ONLY`).
 *
 * Obsługuje oba warianty składni ze specyfikacji COI (por. backend/shared/validators/form_rules.py):
 * - `"Opcja" in tabs['Strona' in title].questions['sekcja' in field.techName].field.repeatingSectionGrid['pole' in field.techName].field.__userAnswer`
 *   (oraz pisownię `repetingSectionGrid`)
 * - `"Opcja" in tabs['Strona' in title].questions['sekcja' in field.techName].field.__userAnswer[$repeatingSectionIndex]['pole' in fieldTechName].answers`
 * oraz operatory logiczne `and`, `or`, `not`.
 */

import type { State } from '../types/coiForm';

export interface StateReference {
  tab: string;
  section: string;
  field: string;
  value: string | null;
  isRepeating: boolean;
}

export type AnswerValue = string | string[] | boolean | number | null | undefined;

/**
 * Mapa odpowiedzi z interaktywnego podglądu formularza.
 * Klucz: `makeAnswerKey(tabTitle, sectionTechName, fieldTechName, repeatingIndex?)`.
 */
export type FormAnswers = Record<string, AnswerValue>;

const REF_PATTERN =
  /(?:"([^"]*)"\s*in\s*)?tabs\[\s*'([^']*)'\s*in\s*title\s*\]\.questions\[\s*'([^']*)'\s*in\s*field\.techName\s*\]\.field\.(?:repea?tingSectionGrid\[\s*'([^']*)'\s*in\s*field\.techName\s*\](?:\.field\.__userAnswer)?|__userAnswer\[\s*\$repeatingSectionIndex\s*\]\[\s*'([^']*)'\s*in\s*fieldTechName\s*\](?:\.answers)?)/g;

export function makeAnswerKey(
  tabTitle: string,
  sectionTechName: string,
  fieldTechName: string,
  repeatingIndex?: number,
): string {
  const base = `${tabTitle}::${sectionTechName}::${fieldTechName}`;
  return repeatingIndex !== undefined ? `${base}#${repeatingIndex}` : base;
}

export function extractStateReferences(query: string): StateReference[] {
  const refs: StateReference[] = [];
  const regex = new RegExp(REF_PATTERN.source, 'g');
  let match: RegExpExecArray | null = regex.exec(query);
  while (match !== null) {
    const [, value, tab, section, regularField, repeatingField] = match;
    refs.push({
      tab,
      section,
      field: regularField || repeatingField || '',
      value: value !== undefined ? value : null,
      isRepeating: Boolean(repeatingField),
    });
    match = regex.exec(query);
  }
  return refs;
}

function answerMatchesValue(answer: AnswerValue, expected: string | null): boolean {
  if (answer === null || answer === undefined) {
    return false;
  }
  if (expected === null) {
    if (Array.isArray(answer)) return answer.length > 0;
    if (typeof answer === 'boolean') return answer;
    return String(answer).trim().length > 0;
  }
  if (Array.isArray(answer)) {
    return answer.includes(expected);
  }
  if (typeof answer === 'boolean') {
    return expected.toLowerCase() === String(answer);
  }
  return String(answer) === expected;
}

function evaluateAtom(atom: string, answers: FormAnswers, repeatingIndex?: number): boolean {
  const trimmed = atom.trim();
  if (!trimmed) return true;

  if (/^not\s*\(([\s\S]*)\)$/i.test(trimmed)) {
    const inner = trimmed.replace(/^not\s*\(([\s\S]*)\)$/i, '$1');
    return !evaluateJsonataCondition(inner, answers, repeatingIndex);
  }
  if (/^not\s+/i.test(trimmed)) {
    const inner = trimmed.replace(/^not\s+/i, '');
    return !evaluateAtom(inner, answers, repeatingIndex);
  }
  if (trimmed.startsWith('!')) {
    return !evaluateAtom(trimmed.slice(1), answers, repeatingIndex);
  }

  const refs = extractStateReferences(trimmed);
  if (refs.length === 0) {
    // Nieznane lub niestandardowe wyrażenie JSONata — nie ukrywamy pola domyślnie.
    return true;
  }

  return refs.every((ref) => {
    const key = ref.isRepeating
      ? makeAnswerKey(ref.tab, ref.section, ref.field, repeatingIndex ?? 0)
      : makeAnswerKey(ref.tab, ref.section, ref.field);
    const fallbackKey = makeAnswerKey(ref.tab, ref.section, ref.field);
    const val = key in answers ? answers[key] : answers[fallbackKey];
    return answerMatchesValue(val, ref.value);
  });
}

function splitTopLevel(expr: string, keyword: 'or' | 'and'): string[] {
  const parts: string[] = [];
  let depth = 0;
  let inSingle = false;
  let inDouble = false;
  let current = '';

  const kwRegex = new RegExp(`^\\s+${keyword}\\s+`, 'i');

  for (let i = 0; i < expr.length; i += 1) {
    const ch = expr[i];
    if (ch === "'" && !inDouble) {
      inSingle = !inSingle;
      current += ch;
      continue;
    }
    if (ch === '"' && !inSingle) {
      inDouble = !inDouble;
      current += ch;
      continue;
    }
    if (!inSingle && !inDouble) {
      if (ch === '(' || ch === '[') depth += 1;
      else if (ch === ')' || ch === ']') depth = Math.max(0, depth - 1);

      if (depth === 0) {
        const rest = expr.slice(i);
        const match = kwRegex.exec(rest);
        if (match) {
          parts.push(current.trim());
          current = '';
          i += match[0].length - 1;
          continue;
        }
      }
    }
    current += ch;
  }
  if (current.trim()) {
    parts.push(current.trim());
  }
  return parts;
}

function unwrapParens(expr: string): string {
  const trimmed = expr.trim();
  if (!trimmed.startsWith('(') || !trimmed.endsWith(')')) return trimmed;
  let depth = 0;
  for (let i = 0; i < trimmed.length - 1; i += 1) {
    if (trimmed[i] === '(') depth += 1;
    else if (trimmed[i] === ')') depth -= 1;
    if (depth === 0) return trimmed;
  }
  return trimmed.slice(1, -1).trim();
}

export function evaluateJsonataCondition(
  query: string,
  answers: FormAnswers,
  repeatingIndex?: number,
): boolean {
  const cleaned = unwrapParens(query);
  if (!cleaned) return true;

  const orParts = splitTopLevel(cleaned, 'or');
  if (orParts.length > 1) {
    return orParts.some((part) => evaluateJsonataCondition(part, answers, repeatingIndex));
  }

  const andParts = splitTopLevel(cleaned, 'and');
  if (andParts.length > 1) {
    return andParts.every((part) => evaluateJsonataCondition(part, answers, repeatingIndex));
  }

  return evaluateAtom(cleaned, answers, repeatingIndex);
}

export interface EvaluatedElementState {
  visible: boolean;
  readOnly: boolean;
  hasShowCondition: boolean;
  hasReadOnlyCondition: boolean;
}

export function evaluateStates(
  states: State[] | undefined,
  answers: FormAnswers,
  baseReadOnly = false,
  repeatingIndex?: number,
): EvaluatedElementState {
  if (!states || states.length === 0) {
    return {
      visible: true,
      readOnly: baseReadOnly,
      hasShowCondition: false,
      hasReadOnlyCondition: false,
    };
  }

  const showStates = states.filter((s) => s.type === 'SHOW');
  const readOnlyStates = states.filter((s) => s.type === 'READ_ONLY');

  const visible =
    showStates.length === 0 ||
    showStates.every((s) => evaluateJsonataCondition(s.query, answers, repeatingIndex));

  const dynamicReadOnly =
    readOnlyStates.length > 0 &&
    readOnlyStates.some((s) => evaluateJsonataCondition(s.query, answers, repeatingIndex));

  return {
    visible,
    readOnly: baseReadOnly || dynamicReadOnly,
    hasShowCondition: showStates.length > 0,
    hasReadOnlyCondition: readOnlyStates.length > 0,
  };
}

/** Zwraca skrócony, czytelny dla urzędnika opis warunku JSONata. */
export function describeStateQuery(query: string): string {
  const refs = extractStateReferences(query);
  if (refs.length === 0) {
    return query.length > 80 ? `${query.slice(0, 77)}…` : query;
  }
  return refs
    .map((ref) =>
      ref.value !== null
        ? `${ref.field} = „${ref.value}”`
        : `${ref.field} wypełnione`,
    )
    .join(' oraz ');
}
