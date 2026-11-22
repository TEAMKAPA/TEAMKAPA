import { useEffect, type ReactNode } from 'react';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: ReactNode;
  maxWidthClass?: string;
}

export function Modal({
  open,
  onClose,
  title,
  subtitle,
  children,
  maxWidthClass = 'max-w-lg',
}: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[#04122F]/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={`w-full ${maxWidthClass} overflow-hidden rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] shadow-none`}
      >
        <div className="flex items-start justify-between gap-4 border-b border-[#E5E7EB] px-6 py-4">
          <div>
            <h2 id="modal-title" className="text-[20px] font-normal leading-7 text-[#0D0E0E]">
              {title}
            </h2>
            {subtitle && (
              <p className="mt-1 text-sm font-normal text-[#52555A]">{subtitle}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Zamknij okno"
            className="rounded-lg border border-[#E5E7EB] bg-[#FFFFFF] px-3 py-1.5 text-sm font-normal text-[#0D0E0E] hover:bg-[#F3F3F4]"
          >
            Zamknij
          </button>
        </div>
        <div className="max-h-[80vh] overflow-y-auto px-6 py-5 font-normal text-[#0D0E0E]">
          {children}
        </div>
      </div>
    </div>
  );
}
