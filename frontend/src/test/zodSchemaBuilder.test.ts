import { describe, expect, it } from 'vitest';
import {
  buildFormZodSchema,
  sanitizeFormForExport,
  validateAde,
  validateEmail,
  validateEpuap,
  validateFormDefinitionRules,
  validateIdCardSerial,
  validateNip,
  validatePesel,
  validateRegon,
  validateZipCode,
} from '../lib/zodSchemaBuilder';
import { makeAnswerKey } from '../lib/conditionEvaluator';
import { normalizeFormDefinition } from '../types/coiForm';
import { SAMPLE_FORM } from '../mocks/sampleResponse';

describe('zodSchemaBuilder — polskie walidatory domenowe i reguły COI', () => {
  it('poprawnie waliduje PESEL, NIP, REGON, kod pocztowy, email, ePUAP, ADE i dowód', () => {
    expect(validatePesel('44051401359')).toBe(true);
    expect(validatePesel('44051401358')).toBe(false);
    expect(validatePesel('12345')).toBe(false);

    expect(validateNip('5260001246')).toBe(true);
    expect(validateNip('526-000-12-46')).toBe(true);
    expect(validateNip('5260001247')).toBe(false);

    expect(validateRegon('123456785')).toBe(true);
    expect(validateRegon('123456789')).toBe(false);

    expect(validateZipCode('00-950')).toBe(true);
    expect(validateZipCode('00950')).toBe(false);

    expect(validateEmail('urzednik@coi.gov.pl')).toBe(true);
    expect(validateEmail('bledny@adres')).toBe(false);

    expect(validateEpuap('/umwarszawa/SkrytkaESP')).toBe(true);
    expect(validateEpuap('umwarszawa')).toBe(false);

    expect(validateAde('AE:PL-12345-67890-ABCDE-12')).toBe(true);
    expect(validateAde('PL-12345')).toBe(false);

    expect(validateIdCardSerial('ABC123456')).toBe(true);
    expect(validateIdCardSerial('123ABC')).toBe(false);
  });

  it('przykładowy formularz SAMPLE_FORM przechodzi reguły COI bez błędów', () => {
    const issues = validateFormDefinitionRules(SAMPLE_FORM);
    const errors = issues.filter((i) => i.severity === 'ERROR');
    expect(errors).toHaveLength(0);
  });

  it('wykrywa naruszenia reguł COI (nieprawidłowe techName, brak readOnly dla danych inicjalnych)', () => {
    const broken = JSON.parse(JSON.stringify(SAMPLE_FORM)) as typeof SAMPLE_FORM;
    const field = broken.tabs[0].questions[2].field.repetingSectionGrid[0].field;
    field.techName = 'bledna nazwa ze spacją';
    if ('readOnly' in field) {
      field.readOnly = false;
    }

    const issues = validateFormDefinitionRules(broken);
    const codes = issues.map((i) => i.code);
    expect(codes).toContain('TECHNAME_INVALID');
    expect(codes).toContain('INITIAL_DATA_NOT_READONLY');
  });

  it('buduje dynamiczny schemat Zod respektujący widoczność pól i sanityzuje eksport', () => {
    const zipKey = makeAnswerKey('Lokalizacja budynku', 'lokalizacja', 'kod_pocztowy');
    const schema = buildFormZodSchema(SAMPLE_FORM, new Set([zipKey]));

    expect(schema.safeParse({ [zipKey]: '00-001' }).success).toBe(true);
    expect(schema.safeParse({ [zipKey]: '99999' }).success).toBe(false);

    const sanitized = sanitizeFormForExport(SAMPLE_FORM);
    expect(sanitized.title).toBe(SAMPLE_FORM.title);
    expect(sanitized.tabs).toHaveLength(SAMPLE_FORM.tabs.length);
  });

  it('obsługuje nowy klucz repeatingSectionGrid, tablicowe defaultValue oraz option.techName=null bez błędów', () => {
    const rawFromBackend = JSON.parse(
      JSON.stringify(SAMPLE_FORM).replaceAll('"repetingSectionGrid"', '"repeatingSectionGrid"'),
    );
    rawFromBackend.tabs[1].questions[0].field.repeatingSectionGrid[0].field.defaultValue = ['Polska'];
    rawFromBackend.tabs[0].questions[1].field.repeatingSectionGrid[0].field.options[0].techName = null;

    const normalized = normalizeFormDefinition(rawFromBackend);
    const issues = validateFormDefinitionRules(normalized);
    expect(issues.filter((i) => i.severity === 'ERROR')).toHaveLength(0);

    const exported = sanitizeFormForExport(normalized);
    expect(exported.tabs[0].questions[0].field).toHaveProperty('repeatingSectionGrid');
  });
});
