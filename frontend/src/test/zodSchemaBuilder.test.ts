import { describe, expect, it } from 'vitest';
import {
  sanitizeFormForExport,
  validateEmail,
  validateFormDefinitionRules,
  validateNip,
  validatePesel,
  validateRegon,
  validateZipCode,
} from '../lib/zodSchemaBuilder';
import { SAMPLE_FORM } from '../mocks/sampleResponse';

describe('polskie walidatory domenowe', () => {
  it('waliduje PESEL (11 cyfr, data urodzenia i suma kontrolna)', () => {
    expect(validatePesel('44051401359')).toBe(true);
    expect(validatePesel('44051401358')).toBe(false);
    expect(validatePesel('12345')).toBe(false);
  });

  it('waliduje NIP, REGON, kod pocztowy i e-mail', () => {
    expect(validateNip('526-025-02-74')).toBe(true);
    expect(validateNip('1234567890')).toBe(false);
    expect(validateRegon('123456785')).toBe(true);
    expect(validateRegon('123456789')).toBe(false);
    expect(validateZipCode('00-950')).toBe(true);
    expect(validateZipCode('00950')).toBe(false);
    expect(validateEmail('urzednik@coi.gov.pl')).toBe(true);
    expect(validateEmail('niepoprawny@')).toBe(false);
  });
});

describe('reguły techniczne COI i sanityzacja eksportu', () => {
  it('wzorcowy formularz SAMPLE_FORM nie ma błędów poziomu ERROR', () => {
    const issues = validateFormDefinitionRules(SAMPLE_FORM);
    const errors = issues.filter((i) => i.severity === 'ERROR');
    expect(errors).toEqual([]);
  });

  it('wykrywa błędy reguł COI (FINISH nie na końcu, zła nazwa techniczna, dane inicjalne bez readOnly)', () => {
    const broken = structuredClone(SAMPLE_FORM);
    broken.tabs[broken.tabs.length - 1].type = 'MAIN';
    const firstField = broken.tabs[0].questions[2].field.repetingSectionGrid[0].field;
    if (firstField.type === 'TEXT_FIELD') {
      firstField.readOnly = false;
      firstField.techName = '123 niepoprawne';
    }

    const codes = validateFormDefinitionRules(broken)
      .filter((i) => i.severity === 'ERROR')
      .map((i) => i.code);

    expect(codes).toContain('FINISH_TAB_LAST');
    expect(codes).toContain('INITIAL_DATA_NOT_READONLY');
    expect(codes).toContain('TECHNAME_INVALID');
  });

  it('sanityzuje eksportowany JSON (usuwa puste/undefined klucze i zachowuje repetingSectionGrid)', () => {
    const exported = sanitizeFormForExport(SAMPLE_FORM);
    expect(exported.tabs[0].questions[0].field.repetingSectionGrid).toHaveLength(1);
    expect(JSON.stringify(exported)).not.toContain('undefined');
  });
});
