import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';

describe('Przepływ urzędnika (upload, przegląd, bbox, edycja, zatwierdzenie)', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline in test'));
  });

  it('waliduje przesyłane pliki w FileDropzone (poprawny PDF i odrzucony ZIP)', async () => {
    const user = userEvent.setup({ applyAccept: false });
    render(<App />);

    const input = screen.getByTestId('file-upload-input') as HTMLInputElement;
    const okFile = new File(['%PDF-1.4'], 'wniosek.pdf', { type: 'application/pdf' });
    const badFile = new File(['PK'], 'archiwum.zip', { type: 'application/zip' });

    await user.upload(input, [okFile, badFile]);

    expect(screen.getByText('wniosek.pdf')).toBeInTheDocument();
    expect(screen.getByText('archiwum.zip')).toBeInTheDocument();
    expect(screen.getByText(/Gotowy do analizy/i)).toBeInTheDocument();
    expect(screen.getByText(/Niedozwolony typ pliku/i)).toBeInTheDocument();
  });

  it('ładuje demo, filtruje niepewne pola, podświetla bbox, edytuje pole i zatwierdza formularz', async () => {
    const user = userEvent.setup();
    render(<App />);

    // 1. Załaduj przykład demo
    await user.click(screen.getByRole('button', { name: /Załaduj przykład demo/i }));

    // Powinniśmy być w widoku przeglądu z tytułem formularza
    expect(
      screen.getByDisplayValue('Wniosek o ustalenie numeru porządkowego nieruchomości'),
    ).toBeInTheDocument();

    // 2. Filtrowanie pól o niskiej pewności (LOW)
    await user.click(screen.getByTestId('filter-LOW'));
    expect(screen.getByTestId('field-card-potwierdzenie_przelewu')).toBeInTheDocument();
    expect(screen.queryByTestId('field-card-nazwa_urzedu')).not.toBeInTheDocument();

    // 3. Kliknięcie pola podświetla jego bbox na obrazie strony i otwiera edytor pola
    await user.click(screen.getByTestId('field-card-potwierdzenie_przelewu'));
    expect(screen.getByTestId('bbox-potwierdzenie_przelewu')).toHaveAttribute(
      'data-selected',
      'true',
    );
    expect(screen.getByTestId('field-editor-panel')).toBeInTheDocument();

    // 4. Edycja etykiety pola
    const titleInput = screen.getByLabelText('Etykieta pola');
    await user.clear(titleInput);
    await user.type(titleInput, 'Potwierdzenie opłaty skarbowej (17 zł)');
    expect(screen.getByTestId('field-card-potwierdzenie_przelewu')).toHaveTextContent(
      'Potwierdzenie opłaty skarbowej (17 zł)',
    );

    // 5. Zatwierdzenie formularza
    await user.click(screen.getByTestId('approve-form-button'));
    await waitFor(() => {
      expect(screen.getByText(/Zatwierdzono \(urzednik \(demo\)\)/i)).toBeInTheDocument();
    });

    // 6. Podgląd JSON zawiera zaktualizowaną etykietę
    await user.click(screen.getByRole('button', { name: /Podgląd JSON/i }));
    expect(screen.getByTestId('json-viewer-code')).toHaveTextContent(
      'Potwierdzenie opłaty skarbowej (17 zł)',
    );
  });

  it('obsługuje interaktywny podgląd wypełniania (warunki JSONata SHOW i walidację Zod)', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole('button', { name: /Załaduj przykład demo/i }));
    await user.click(screen.getByRole('button', { name: /Podgląd wypełniania/i }));

    // Sekcja warunkowa "Dane innej osoby" jest ukryta dopóki nie wybierzemy "Innej osoby"
    expect(screen.queryByText('Dane innej osoby')).not.toBeInTheDocument();

    await user.click(screen.getByLabelText('Innej osoby'));
    expect(screen.getByText('Dane innej osoby')).toBeInTheDocument();

    // Walidacja pustych wymaganych pól wykazuje błędy
    await user.click(screen.getByRole('button', { name: /Sprawdź walidację formularza/i }));
    expect(screen.getByText(/Wykryto błędy wypełnienia/i)).toBeInTheDocument();
  });
});
