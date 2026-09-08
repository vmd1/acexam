import { useEffect, useState } from 'react';
import { BrowserRouter as Router } from 'react-router-dom';
import { GraduationCap, Sun, Moon, Construction } from 'lucide-react';
import Landing from './Landing';

type Theme = 'light' | 'dark';

function getInitialTheme(): Theme {
  const stored = localStorage.getItem('theme');
  if (stored === 'light' || stored === 'dark') return stored;
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

// Standalone shell for VITE_HOMEPAGE_ONLY builds: just the marketing
// homepage plus a "coming soon" banner, no auth/router-guards/API calls -
// safe to deploy with no backend hosted at all. See main.tsx for the
// build-time switch and Landing.tsx for the homepage-only CTA behavior.
export default function HomepageOnlyShell() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  return (
    <Router>
      <div className="coming-soon-banner">
        <Construction size={16} aria-hidden="true" />
        <span>Acexam is still in development - you're looking at a preview of the homepage only.</span>
      </div>
      <header className="topbar">
        <div className="topbar-left">
          <span className="topbar-brand">
            <GraduationCap size={22} aria-hidden="true" />
            Acexam
          </span>
        </div>
        <div className="topbar-actions">
          <button
            onClick={() => setTheme(prev => (prev === 'light' ? 'dark' : 'light'))}
            className="theme-toggle"
            aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
          >
            {theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}
          </button>
        </div>
      </header>
      <main className="no-sidebar" style={{ marginLeft: 'auto' }}>
        <Landing />
      </main>
    </Router>
  );
}
