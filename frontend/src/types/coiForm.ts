/**
 * Modele konfiguracji e-formularza COI (1:1 z backend/shared/schemas/coi_form.py
 * oraz backend/shared/schemas/initial_data.py).
 *
 * Klucze JSON są w camelCase, zgodnie ze schematem importu COI — w tym celowa pisownia
 * `repetingSectionGrid`.
 */

export const MAX_FORM_TITLE_LENGTH = 255;
export const TECH_NAME_REGEX = /^[A-Za-z_][A-Za-z0-9_]*$/;

export type TabType = 'MAIN' | 'FINISH';

export type FieldType =
  | 'SECTION'
  | 'REPEATING_SECTION'
  | 'TEXT_FIELD'
  | 'CHECKBOX'
  | 'NUMBER'
  | 'CONSENT_SECTION'
  | 'SELECT'
  | 'TEXTAREA'
  | 'FILE_UPLOADER'
  | 'RADIO'
  | 'DATEPICKER'
  | 'TEXT_BLOCK'
  | 'INFO_SECTION';

export type LeafFieldType = Exclude<FieldType, 'SECTION' | 'REPEATING_SECTION'>;
export type SectionFieldType = 'SECTION' | 'REPEATING_SECTION';

export type Validator =
  | 'REQUIRED'
  | 'EMAIL'
  | 'PESEL'
  | 'NIP'
  | 'REGON'
  | 'ZIP_CODE'
  | 'EPUAP'
  | 'ADE'
  | 'SERIAL_NUMBER_ID_CARD';

export type RequiredOnlyValidator = 'REQUIRED';

export type StateType = 'SHOW' | 'READ_ONLY';

export type DefaultValueType = 'EXTERNAL_SYSTEM';

export const ALL_VALIDATORS: readonly Validator[] = [
  'REQUIRED',
  'EMAIL',
  'PESEL',
  'NIP',
  'REGON',
  'ZIP_CODE',
  'EPUAP',
  'ADE',
  'SERIAL_NUMBER_ID_CARD',
] as const;

export const VALIDATOR_LABELS: Record<Validator, string> = {
  REQUIRED: 'Wymagane',
  EMAIL: 'Adres e-mail',
  PESEL: 'PESEL',
  NIP: 'NIP',
  REGON: 'REGON',
  ZIP_CODE: 'Kod pocztowy (XX-XXX)',
  EPUAP: 'Adres ePUAP',
  ADE: 'Adres e-Doręczeń (ADE)',
  SERIAL_NUMBER_ID_CARD: 'Seria i numer dowodu',
};

export const INPUT_FIELD_TYPES: ReadonlySet<LeafFieldType> = new Set([
  'TEXT_FIELD',
  'CHECKBOX',
  'NUMBER',
  'SELECT',
  'TEXTAREA',
  'FILE_UPLOADER',
  'RADIO',
  'DATEPICKER',
]);

export const CHOICE_FIELD_TYPES: ReadonlySet<LeafFieldType> = new Set([
  'CHECKBOX',
  'SELECT',
  'RADIO',
]);

export const FIELD_TYPE_LABELS: Record<FieldType, string> = {
  SECTION: 'Sekcja',
  REPEATING_SECTION: 'Sekcja powtarzalna',
  TEXT_FIELD: 'Pole tekstowe',
  CHECKBOX: 'Wielokrotny wybór (Checkbox)',
  NUMBER: 'Pole liczbowe',
  CONSENT_SECTION: 'Sekcja zgód',
  SELECT: 'Lista rozwijana (Select)',
  TEXTAREA: 'Pole wieloliniowe',
  FILE_UPLOADER: 'Załącznik (Plik)',
  RADIO: 'Jednokrotny wybór (Radio)',
  DATEPICKER: 'Data',
  TEXT_BLOCK: 'Blok tekstu',
  INFO_SECTION: 'Sekcja informacyjna',
};

export interface Option {
  id: string;
  name: string;
  techName: string;
}

export interface State {
  type: StateType;
  query: string;
}

export interface ShowState {
  type: 'SHOW';
  query: string;
}

export interface Consent {
  description: string;
  required?: boolean;
}

export interface ConsentSectionConfig {
  consents: Consent[];
  required?: boolean;
  files: string[];
}

export interface BaseField {
  id: string;
  techName: string;
  questionOrder?: number;
  tooltipMessage?: string;
}

export interface ChoiceMixin {
  options: Option[];
  dictionaryName?: string;
}

export interface DefaultValueMixin {
  defaultValue?: string;
  defaultValueType?: DefaultValueType;
}

export interface TextField extends BaseField, DefaultValueMixin {
  type: 'TEXT_FIELD';
  title?: string;
  description?: string;
  readOnly?: boolean;
  validation: Validator[];
  states?: State[];
}

export interface CheckboxField extends BaseField, ChoiceMixin {
  type: 'CHECKBOX';
  title?: string;
  description?: string;
  readOnly?: boolean;
  validation: RequiredOnlyValidator[];
  states?: State[];
}

export interface NumberField extends BaseField {
  type: 'NUMBER';
  title?: string;
  description?: string;
  readOnly?: boolean;
  validation: RequiredOnlyValidator[];
  states?: State[];
}

export interface SelectField extends BaseField, ChoiceMixin, DefaultValueMixin {
  type: 'SELECT';
  title?: string;
  description?: string;
  readOnly?: boolean;
  searchEnabled?: boolean;
  validation: RequiredOnlyValidator[];
  states?: State[];
}

export interface TextareaField extends BaseField, DefaultValueMixin {
  type: 'TEXTAREA';
  title?: string;
  description?: string;
  readOnly?: boolean;
  validation: RequiredOnlyValidator[];
  states?: State[];
}

export interface FileUploaderField extends BaseField {
  type: 'FILE_UPLOADER';
  title?: string;
  description?: string;
  defaultValue?: string;
  validation: RequiredOnlyValidator[];
  states?: State[];
}

export interface RadioField extends BaseField, ChoiceMixin {
  type: 'RADIO';
  title?: string;
  description?: string;
  readOnly?: boolean;
  validation: RequiredOnlyValidator[];
  states?: State[];
}

export interface DatepickerField extends BaseField, DefaultValueMixin {
  type: 'DATEPICKER';
  title?: string;
  description?: string;
  readOnly?: boolean;
  validation: RequiredOnlyValidator[];
  states?: State[];
}

export interface TextBlockField extends BaseField {
  type: 'TEXT_BLOCK';
  title?: string;
  description?: string;
  states?: ShowState[];
}

export interface ConsentSectionField extends BaseField {
  type: 'CONSENT_SECTION';
  selectAllEnabled?: boolean;
  consentSectionConfig: ConsentSectionConfig;
}

export interface InfoSectionField extends BaseField {
  type: 'INFO_SECTION';
  description: string;
}

export type LeafField =
  | TextField
  | CheckboxField
  | NumberField
  | SelectField
  | TextareaField
  | FileUploaderField
  | RadioField
  | DatepickerField
  | TextBlockField
  | ConsentSectionField
  | InfoSectionField;

export type ChoiceField = CheckboxField | SelectField | RadioField;

export interface GridPosition {
  x: number;
  y: number;
  rows: number;
  cols: number;
}

export interface FieldQuestion extends GridPosition {
  field: LeafField;
}

export interface SectionField extends BaseField {
  type: SectionFieldType;
  title: string;
  states?: ShowState[];
  /** Pisownia celowo zgodna ze schematem COI (`importowanyPlikJsonSchemat.json`). */
  repetingSectionGrid: FieldQuestion[];
}

export interface SectionQuestion extends GridPosition {
  field: SectionField;
}

export interface Tab {
  id: string;
  title: string;
  type: TabType;
  questions: SectionQuestion[];
}

export interface FlowLink {
  id: string;
  source: string;
  target: string;
}

export interface Flow {
  links: FlowLink[];
}

export interface FormDefinition {
  title: string;
  tabs: Tab[];
  flow: Flow;
}

/** 6 oficjalnych słowników COI oraz dozwolone typy pól. */
export const COI_DICTIONARIES: Record<string, readonly ('SELECT' | 'RADIO' | 'CHECKBOX')[]> = {
  Województwo: ['SELECT'],
  Powiat: ['SELECT'],
  Gmina: ['SELECT'],
  Miejscowość: ['SELECT'],
  Kraje: ['SELECT'],
  'Numery kierunkowe': ['SELECT', 'RADIO', 'CHECKBOX'],
};

export type InitialDataSource = 'PESEL' | 'PESEL_ADDRESS' | 'MLPZ';

export interface InitialDataEntry {
  reference: string;
  source: InitialDataSource;
  attribute: string;
  label: string;
  fieldType: 'TEXT_FIELD' | 'DATEPICKER' | null;
}

const RAW_INITIAL_DATA: readonly [InitialDataSource, string, string, 'TEXT_FIELD' | 'DATEPICKER' | null][] = [
  // PESEL
  ['PESEL', 'pesel', 'Pesel z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'name', 'Imię z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'surname', 'Nazwisko z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'birthDate', 'Data urodzenia z PESEL', 'DATEPICKER'],
  ['PESEL', 'citizenship', 'Narodowość z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'motherName', 'Imię matki z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'birthPlace', 'Miejsce urodzenia z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'motherSurname', 'Nazwisko matki z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'sex', 'Płeć z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'fatherName', 'Imię ojca z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'secondName', 'Drugie imię z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'birthCountry', 'Kraj urodzenia z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'foreignBirthUSC', 'Zagraniczne miasto USC urodzenia z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'familyName', 'Nazwisko rodowe z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'fatherSurname', 'Nazwisko ojca z PESEL', 'TEXT_FIELD'],
  ['PESEL', 'maritalStatus', 'Stan cywilny z PESEL', null],
  // PESEL_ADDRESS
  ['PESEL_ADDRESS', 'temporaryAddressVoivodeship', 'Tymczasowy adres zameldowania: województwo z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'temporaryAddressDistrict', 'Tymczasowy adres zameldowania: region z PESEL', null],
  ['PESEL_ADDRESS', 'temporaryAddressCommunity', 'Tymczasowy adres zameldowania: gmina z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'temporaryAddressPlace', 'Tymczasowy adres zameldowania: Miejscowość z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'temporaryAddressStreetType', 'Tymczasowy adres zameldowania: Typ ulicy z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'temporaryAddressStreet', 'Tymczasowy adres zameldowania: Ulica z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'temporaryAddressHouseNumber', 'Tymczasowy adres zameldowania: Numer domu z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'temporaryAddressApartmentNumber', 'Tymczasowy adres zameldowania: Numer lokalu z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'temporaryAddressPostalCode', 'Tymczasowy adres zameldowania: Kod pocztowy z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'permanentAddressVoivodeship', 'Stały adres zameldowania: województwo z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'permanentAddressDistrict', 'Stały adres zameldowania: region z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'permanentAddressCommunity', 'Stały adres zameldowania: gmina z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'permanentAddressPlace', 'Stały adres zameldowania: Miejscowość z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'permanentAddressStreetType', 'Stały adres zameldowania: Typ ulicy z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'permanentAddressStreet', 'Stały adres zameldowania: Ulica z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'permanentAddressPostalCode', 'Stały adres zameldowania: Kod pocztowy z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'permanentAddressHouseNumber', 'Stały adres zameldowania: Numer domu z PESEL', 'TEXT_FIELD'],
  ['PESEL_ADDRESS', 'permanentAddressApartmentNumber', 'Stały adres zameldowania: Numer lokalu z PESEL', 'TEXT_FIELD'],
  // MLPZ
  ['MLPZ', 'name', 'Imię z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'secondName', 'Drugie Imię z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'surname', 'Nazwisko z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'pesel', 'Wartość PESEL z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'phoneNumber', 'Numer telefonu z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'email', 'Adres email z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'country', 'Kraj z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'voivodeship', 'Województwo z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'district', 'Dzielnica z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'community', 'Gmina z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'place', 'Miejscowość z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'postCode', 'Kod pocztowy z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'street', 'Ulica z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'building', 'Nr budynku z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'apartment', 'Nr lokalu z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'institutionName', 'Nazwa Organizacji z MLPZ', 'TEXT_FIELD'],
  ['MLPZ', 'descriptiveOrganizationName', 'Opisowa nazwa organizacji z MLPZ', 'TEXT_FIELD'],
];

export const INITIAL_DATA_LIST: readonly InitialDataEntry[] = RAW_INITIAL_DATA.map(
  ([source, attribute, label, fieldType]) => ({
    reference: `${source}.${attribute}`,
    source,
    attribute,
    label,
    fieldType,
  }),
);

export const INITIAL_DATA_CATALOG: ReadonlyMap<string, InitialDataEntry> = new Map(
  INITIAL_DATA_LIST.map((entry) => [entry.reference, entry]),
);

export function isChoiceField(field: LeafField): field is ChoiceField {
  return field.type === 'SELECT' || field.type === 'RADIO' || field.type === 'CHECKBOX';
}

export function isInputField(field: LeafField): boolean {
  return INPUT_FIELD_TYPES.has(field.type);
}

export function getFieldTitle(field: LeafField): string {
  if ('title' in field && field.title) {
    return field.title;
  }
  if (field.type === 'CONSENT_SECTION') {
    return 'Sekcja zgód';
  }
  if (field.type === 'INFO_SECTION') {
    return 'Informacja';
  }
  return field.techName;
}
