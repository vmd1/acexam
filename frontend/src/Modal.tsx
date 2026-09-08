import React, { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

interface ModalProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}

// Minimal accessible modal: traps Escape-to-close, locks body scroll while
// open, and focuses itself on mount so keyboard users land inside it.
export default function Modal({ title, onClose, children }: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  // Read via a ref rather than depending on `onClose` directly - most
  // callers pass an inline/non-memoized handler that gets a new identity on
  // every render (e.g. every keystroke in a form inside this modal), and
  // this effect's own body calls dialogRef.current?.focus() on every run -
  // depending on `onClose` would steal focus off whatever the user is
  // typing into and back onto the modal container after every character.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCloseRef.current();
    };
    document.addEventListener('keydown', onKeyDown);
    document.body.style.overflow = 'hidden';
    dialogRef.current?.focus();
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = '';
    };
    // Mount/unmount only - see onCloseRef above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        ref={dialogRef}
        tabIndex={-1}
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 id="modal-title" style={{ marginBottom: 0 }}>{title}</h2>
          <button onClick={onClose} className="modal-close-btn" aria-label="Close dialog">
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
