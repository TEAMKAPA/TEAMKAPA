import { useState } from 'react';

export type AppSectionView = 'upload' | 'review';

interface NavbarProps {
  activeView: AppSectionView;
  demoMode: boolean;
  hasToken: boolean;
  hasActiveForm: boolean;
  onSelectView: (view: AppSectionView) => void;
  onToggleDemo: () => void;
  onOpenAuth: () => void;
}

/**
 * Górny pasek aplikacji roboczej (64px wysokości, tło #04122F).
 * Treść ograniczona do max-w-[1400px], aby napisy były bliżej środka
 * i wyrównane z głównym obszarem roboczym.
 */
export function Navbar({
  activeView,
  demoMode,
  hasToken,
  hasActiveForm,
  onSelectView,
  onToggleDemo,
  onOpenAuth,
}: NavbarProps) {
  const [mobileOpen, setMobileOpen] = useState(false);

  const linkClass = (isActive: boolean) =>
    `text-[15px] font-normal leading-6 transition-colors ${
      isActive ? 'text-white underline underline-offset-4' : 'text-white/75 hover:text-white'
    }`;

  const handleNavClick = (view: AppSectionView) => {
    setMobileOpen(false);
    onSelectView(view);
  };

  return (
    <header className="w-full bg-[#04122F] text-white">
      <div className="mx-auto flex h-16 max-w-[1400px] items-center justify-between gap-6 px-4 sm:px-6">
        <button
          type="button"
          onClick={() => handleNavClick('upload')}
          className="text-left text-[17px] font-normal leading-6 text-white sm:text-[18px]"
        >
          Cyfryzacja formularzy
        </button>

        {/* Główne widoki bliżej środka na desktopie */}
        <nav aria-label="Nawigacja aplikacji" className="hidden items-center gap-8 md:flex">
          <button
            type="button"
            onClick={() => handleNavClick('upload')}
            className={linkClass(activeView === 'upload')}
          >
            Wgrywanie plików
          </button>

          <button
            type="button"
            onClick={() => handleNavClick('review')}
            className={linkClass(activeView === 'review')}
            title={hasActiveForm ? 'Otwórz bieżący formularz' : 'Otwórz przykładowy formularz do weryfikacji'}
          >
            Weryfikacja
          </button>
        </nav>

        {/* Ustawienia sesji po prawej */}
        <div className="hidden items-center gap-6 md:flex">
          <button
            type="button"
            onClick={onOpenAuth}
            className="text-[15px] font-normal leading-6 text-white/75 transition-colors hover:text-white"
          >
            {hasToken ? 'Token zapisany' : 'Token API'}
          </button>

          <button
            type="button"
            onClick={onToggleDemo}
            className="text-[15px] font-normal leading-6 text-white/75 transition-colors hover:text-white"
          >
            {demoMode ? 'Tryb demo: wł.' : 'Tryb demo'}
          </button>
        </div>

        {/* Przycisk hamburgera dla urządzeń mobilnych */}
        <button
          type="button"
          onClick={() => setMobileOpen((prev) => !prev)}
          aria-label={mobileOpen ? 'Zamknij menu' : 'Otwórz menu'}
          aria-expanded={mobileOpen}
          aria-controls="mobile-nav-menu"
          className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-white/20 text-white md:hidden"
        >
          {mobileOpen ? (
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <line x1="3" y1="3" x2="15" y2="15" stroke="currentColor" strokeWidth="1.5" />
              <line x1="15" y1="3" x2="3" y2="15" stroke="currentColor" strokeWidth="1.5" />
            </svg>
          ) : (
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <line x1="2" y1="4.5" x2="16" y2="4.5" stroke="currentColor" strokeWidth="1.5" />
              <line x1="2" y1="9" x2="16" y2="9" stroke="currentColor" strokeWidth="1.5" />
              <line x1="2" y1="13.5" x2="16" y2="13.5" stroke="currentColor" strokeWidth="1.5" />
            </svg>
          )}
        </button>
      </div>

      {/* Rozwijane menu mobilne */}
      {mobileOpen && (
        <nav
          id="mobile-nav-menu"
          aria-label="Menu mobilne"
          className="border-t border-white/15 bg-[#04122F] px-4 py-3 md:hidden"
        >
          <div className="flex flex-col space-y-1">
            <button
              type="button"
              onClick={() => handleNavClick('upload')}
              className={`py-2.5 text-left ${linkClass(activeView === 'upload')}`}
            >
              Wgrywanie plików
            </button>
            <button
              type="button"
              onClick={() => handleNavClick('review')}
              className={`py-2.5 text-left ${linkClass(activeView === 'review')}`}
            >
              Weryfikacja
            </button>
            <button
              type="button"
              onClick={() => {
                setMobileOpen(false);
                onOpenAuth();
              }}
              className="py-2.5 text-left text-[15px] font-normal text-white/75 hover:text-white"
            >
              {hasToken ? 'Token zapisany' : 'Token API'}
            </button>
            <button
              type="button"
              onClick={() => {
                setMobileOpen(false);
                onToggleDemo();
              }}
              className="py-2.5 text-left text-[15px] font-normal text-white/75 hover:text-white"
            >
              {demoMode ? 'Tryb demo: wł.' : 'Tryb demo'}
            </button>
          </div>
        </nav>
      )}
    </header>
  );
}
