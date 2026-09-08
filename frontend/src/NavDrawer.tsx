import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { GraduationCap, X, LogIn, UserPlus, Home, Sun, Moon } from 'lucide-react';

type Theme = 'light' | 'dark';

interface NavDrawerProps {
  onClose: () => void;
  theme: Theme;
  onToggleTheme: () => void;
}

// Logged-out hamburger drawer (revise2-style): slide-in-from-left overlay
// with the marketing nav links plus theme toggle, mirroring Modal.tsx's
// escape/backdrop/focus/body-scroll-lock pattern rather than reinventing it.
export default function NavDrawer({ onClose, theme, onToggleTheme }: NavDrawerProps) {
  const drawerRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCloseRef.current();
    };
    document.addEventListener('keydown', onKeyDown);
    document.body.style.overflow = 'hidden';
    drawerRef.current?.focus();
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = '';
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="nav-drawer-backdrop" onClick={onClose}>
      <div
        className="nav-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Menu"
        ref={drawerRef}
        tabIndex={-1}
        onClick={e => e.stopPropagation()}
      >
        <div className="nav-drawer-header">
          <Link to="/" className="topbar-brand" onClick={onClose}>
            <GraduationCap size={22} aria-hidden="true" />
            Acexam
          </Link>
          <button onClick={onClose} className="modal-close-btn" aria-label="Close menu">
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        <nav className="nav-drawer-links">
          <Link to="/" className="nav-drawer-link" onClick={onClose}>
            <Home size={18} aria-hidden="true" />
            Home
          </Link>
          <Link to="/login" className="nav-drawer-link" onClick={onClose}>
            <LogIn size={18} aria-hidden="true" />
            Log in
          </Link>
          <Link to="/onboarding" className="nav-drawer-link" onClick={onClose}>
            <UserPlus size={18} aria-hidden="true" />
            Sign up free
          </Link>
        </nav>

        <div className="nav-drawer-footer">
          <div className="sidebar-legal-links" style={{ marginBottom: '0.75rem' }}>
            <Link to="/terms" onClick={onClose}>Terms</Link>
            <span aria-hidden="true">·</span>
            <Link to="/privacy" onClick={onClose}>Privacy</Link>
          </div>
          <button
            onClick={onToggleTheme}
            className="btn btn-outline"
            style={{ width: '100%' }}
            aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
          >
            {theme === 'light' ? <Moon size={16} aria-hidden="true" /> : <Sun size={16} aria-hidden="true" />}
            {theme === 'light' ? 'Dark mode' : 'Light mode'}
          </button>
        </div>
      </div>
    </div>
  );
}
