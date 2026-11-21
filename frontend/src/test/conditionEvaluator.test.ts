import { describe, expect, it } from 'vitest';
import {
  describeStateQuery,
  evaluateJsonataCondition,
  evaluateStates,
  extractStateReferences,
  makeAnswerKey,
} from '../lib/conditionEvaluator';

describe('conditionEvaluator', () => {
  const sampleQuery =
    '"Innej osoby" in tabs[\'Dane wnioskodawcy\' in title].questions[\'podmiot_wniosku\' in field.techName].field.repeatingSectionGrid[\'w_czyim_imieniu\' in field.techName].field.__userAnswer';

  it('wyciąga odwołania z zapytania JSONata (obie pisownie repeating/repeting)', () => {
    const refs = extractStateReferences(sampleQuery);
    expect(refs).toHaveLength(1);
    expect(refs[0]).toMatchObject({
      tab: 'Dane wnioskodawcy',
      section: 'podmiot_wniosku',
      field: 'w_czyim_imieniu',
      value: 'Innej osoby',
      isRepeating: false,
    });

    const typoQuery = sampleQuery.replace('repeatingSectionGrid', 'repetingSectionGrid');
    expect(extractStateReferences(typoQuery)).toHaveLength(1);
  });

  it('poprawnie ocenia warunek SHOW dla pasującej i niepasującej odpowiedzi', () => {
    const key = makeAnswerKey('Dane wnioskodawcy', 'podmiot_wniosku', 'w_czyim_imieniu');

    expect(evaluateJsonataCondition(sampleQuery, { [key]: 'Innej osoby' })).toBe(true);
    expect(evaluateJsonataCondition(sampleQuery, { [key]: 'Swoim' })).toBe(false);
    expect(evaluateJsonataCondition(sampleQuery, {})).toBe(false);
  });

  it('obsługuje sekcje powtarzalne ($repeatingSectionIndex) oraz operatory logiczne', () => {
    const repQuery =
      '"Tak" in tabs[\'Dane\' in title].questions[\'osoby\' in field.techName].field.__userAnswer[$repeatingSectionIndex][\'czy_pelnoletni\' in fieldTechName].answers';
    const key0 = makeAnswerKey('Dane', 'osoby', 'czy_pelnoletni', 0);
    const key1 = makeAnswerKey('Dane', 'osoby', 'czy_pelnoletni', 1);

    expect(evaluateJsonataCondition(repQuery, { [key0]: 'Tak', [key1]: 'Nie' }, 0)).toBe(true);
    expect(evaluateJsonataCondition(repQuery, { [key0]: 'Tak', [key1]: 'Nie' }, 1)).toBe(false);
  });

  it('zwraca czytelny opis warunku i stan READ_ONLY', () => {
    expect(describeStateQuery(sampleQuery)).toContain('w_czyim_imieniu');
    const key = makeAnswerKey('Dane wnioskodawcy', 'podmiot_wniosku', 'w_czyim_imieniu');
    const result = evaluateStates(
      [
        { type: 'SHOW', query: sampleQuery },
        { type: 'READ_ONLY', query: sampleQuery },
      ],
      { [key]: 'Innej osoby' },
    );
    expect(result.visible).toBe(true);
    expect(result.readOnly).toBe(true);
  });
});
