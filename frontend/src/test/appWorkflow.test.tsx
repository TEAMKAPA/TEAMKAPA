import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';

describe('Cyfryzacja formularzy — ścieżka robocza urzędnika', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'));
  });

  it('odrzuca niedozwolony plik .zip w strefie wgrywania plików', async () => {
    const user = userEvent.setup({ applyAccept: false });
    render(<App />);

    const input = screen.getByTestId('file-upload-input');
    const badFile = new File(['abc'], 'archiwum.zip', { type: 'application/zip' });
    await user.upload(input, badFile);

    expect(screen.getByText('archiwum.zip')).toBeInTheDocument();
    expect(screen.getByText(/Niedozwolony typ pliku/i)).toBeInTheDocument();
    expect(screen.getByText(/odrzuconych: 1/i)).toBeInTheDocument();
  });

  it('otwiera i zamyka menu mobilne (hamburger)', async () => {
    const user = userEvent.setup();
    render(<App />);

    const burger = screen.getByRole('button', { name: 'Otwórz menu' });
    expect(burger).toHaveAttribute('aria-expanded', 'false');

    await user.click(burger);
    expect(screen.getByRole('navigation', { name: 'Menu mobilne' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Zamknij menu' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('otwiera weryfikację, filtruje niską pewność, podświetla bbox, edytuje pole i zatwierdza', async () => {
    const user = userEvent.setup();
    render(<App />);

    // 1. Przejście do widoku weryfikacji
    await user.click(screen.getByRole('button', { name: 'Załaduj przykład' }));

    expect(screen.getByRole('heading', { name: 'Podgląd dokumentu' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Rozpoznane pola' })).toBeInTheDocument();
    expect(screen.getByLabelText('Tytuł formularza')).toHaveValue(
      'Wniosek o ustalenie numeru porządkowego nieruchomości',
    );

    // 2. Filtrowanie pól z niską pewnością
    await user.selectOptions(screen.getByTestId('review-filter-select'), 'LOW');
    const lowFieldRow = screen.getByTestId('field-card-potwierdzenie_przelewu');
    expect(lowFieldRow).toBeInTheDocument();
    expect(within(lowFieldRow).getByText('Pewność: 42%')).toBeInTheDocument();

    // 3. Edycja pola tekstowego w wierszu i podświetlenie bbox na podglądzie dokumentu
    const input = within(lowFieldRow).getByRole('textbox');
    await user.clear(input);
    await user.type(input, 'Dowód opłaty skarbowej 17 zł');

    const bbox = screen.getByTestId('bbox-potwierdzenie_przelewu');
    expect(bbox).toHaveAttribute('data-selected', 'true');

    // 4. Zatwierdzenie i eksport
    const approveBtn = screen.getByTestId('approve-form-button');
    await user.click(approveBtn);
    expect(screen.getByText('Zatwierdzono')).toBeInTheDocument();

    // 5. Weryfikacja zaktualizowanej etykiety w podglądzie JSON
    await user.click(screen.getByRole('tab', { name: 'Podgląd JSON' }));
    expect(screen.getByTestId('json-viewer-code')).toHaveTextContent('Dowód opłaty skarbowej 17 zł');

    // 6. Podgląd wypełniania — dynamiczna widoczność JSONata SHOW + walidacja Zod
    await user.click(screen.getByRole('tab', { name: 'Podgląd wypełniania' }));
    expect(screen.queryByText('Dane innej osoby')).not.toBeInTheDocument();

    await user.click(screen.getByLabelText('Innej osoby'));
    expect(screen.getByText('Dane innej osoby')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Sprawdź walidację formularza' }));
    expect(screen.getByText(/Wykryto błędy wypełnienia/i)).toBeInTheDocument();
  });
});
