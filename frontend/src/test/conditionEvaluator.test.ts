import { describe, expect, it } from 'vitest';
import {
  describeStateQuery,
  evaluateJsonataCondition,
  evaluateStates,
  extractStateReferences,
  makeAnswerKey,
} from '../lib/conditionEvaluator';

const SAMPLE_QUERY =
  '"Innej osoby" in tabs[\'Dane wnioskodawcy\' in title].questions[\'podmiot_wniosku\' in field.techName].field.repeatingSectionGrid[\'w_czyim_imieniu\' in field.techName].field.__userAnswer';

describe('conditionEvaluator (JSONata SHOW / READ_ONLY)', () => {
  it('ekstrahuje referencje ze standardowego zapytania COI', () => {
    const refs = extractStateReferences(SAMPLE_QUERY);
    expect(refs).toHaveLength(1);
    expect(refs[0]).toEqual({
      tab: 'Dane wnioskodawcy',
      section: 'podmiot_wniosku',
      field: 'w_czyim_imieniu',
      value: 'Innej osoby',
      isRepeating: false,
    });
  });

  it('ewaluuje warunek SHOW dla odpowiedzi pojedynczej i wielokrotnej', () => {
    const key = makeAnswerKey('Dane wnioskodawcy', 'podmiot_wniosku', 'w_czyim_imieniu');
    expect(evaluateJsonataCondition(SAMPLE_QUERY, {})).toBe(false);
    expect(evaluateJsonataCondition(SAMPLE_QUERY, { [key]: 'Swoim' })).toBe(false);
    expect(evaluateJsonataCondition(SAMPLE_QUERY, { [key]: 'Innej osoby' })).toBe(true);
    expect(evaluateJsonataCondition(SAMPLE_QUERY, { [key]: ['Swoim', 'Innej osoby'] })).toBe(true);
  });

  it('obsługuje operatory logiczne or / and / not oraz READ_ONLY', () => {
    const key = makeAnswerKey('Dane wnioskodawcy', 'podmiot_wniosku', 'w_czyim_imieniu');
    const negQuery = `not(${SAMPLE_QUERY})`;
    expect(evaluateJsonataCondition(negQuery, { [key]: 'Swoim' })).toBe(true);
    expect(evaluateJsonataCondition(negQuery, { [key]: 'Innej osoby' })).toBe(false);

    const state = evaluateStates(
      [
        { type: 'SHOW', query: SAMPLE_QUERY },
        { type: 'READ_ONLY', query: SAMPLE_QUERY },
      ],
      { [key]: 'Innej osoby' },
    );
    expect(state.visible).toBe(true);
    expect(state.readOnly).toBe(true);
  });

  it('generuje czytelny opis warunku dla urzędnika', () => {
    expect(describeStateQuery(SAMPLE_QUERY)).toContain('w_czyim_imieniu');
    expect(describeStateQuery(SAMPLE_QUERY)).toContain('Innej osoby');
  });
});
