import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import api from './api';
import { Sparkles, Target, TrendingUp, BookOpen, PenLine, Zap, Check, X, ShieldCheck } from 'lucide-react';

interface Qualification {
  exam_board: string;
  level: string;
  subject: string;
  tiers: string[];
}

// Set for VITE_HOMEPAGE_ONLY builds (see main.tsx/HomepageOnlyShell) - no
// backend is hosted in that mode, so skip the qualifications fetch and
// swap the CTA from "start onboarding" to a no-op "coming soon" state.
const HOMEPAGE_ONLY = import.meta.env.VITE_HOMEPAGE_ONLY === 'true';

// Decorative, aria-hidden blobs that drift slowly behind the hero (pure
// CSS animation, see .landing-blob in index.css) - purely cosmetic, so they
// carry no semantics and respect prefers-reduced-motion.
function LandingBlobs() {
  return (
    <svg className="landing-blob" viewBox="0 0 600 600" aria-hidden="true" focusable="false">
      <defs>
        <filter id="landing-blob-blur" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="40" />
        </filter>
      </defs>
      <g filter="url(#landing-blob-blur)">
        <circle className="landing-blob-a" cx="150" cy="180" r="150" fill="var(--accent-color)" />
        <circle className="landing-blob-b" cx="460" cy="360" r="120" fill="var(--info)" />
      </g>
    </svg>
  );
}

// Logged-out hook page at "/", styled after revise2.com: a bold, centered
// headline, a level toggle, the supported exam boards, and one big CTA -
// no navbar, no feature-tour clutter above the fold. The hamburger (top
// bar, see App.tsx) carries the rest of the marketing nav.
export default function Landing() {
  const navigate = useNavigate();
  const [levels, setLevels] = useState<string[]>(['GCSE', 'A-Level']);
  const [boards, setBoards] = useState<string[]>(['AQA', 'Edexcel', 'OCR']);
  const [selectedLevel, setSelectedLevel] = useState('GCSE');

  useEffect(() => {
    if (HOMEPAGE_ONLY) return;
    (async () => {
      try {
        const res = await api.get('/exams/qualifications');
        const quals: Qualification[] = res.data;
        if (quals.length > 0) {
          setLevels(Array.from(new Set(quals.map(q => q.level))));
          setBoards(Array.from(new Set(quals.map(q => q.exam_board))));
        }
      } catch {
        // Public catalogue endpoint - if it's unreachable, the static
        // fallback lists above still render a sensible hero.
      }
    })();
  }, []);

  useEffect(() => {
    if (levels.length > 0 && !levels.includes(selectedLevel)) setSelectedLevel(levels[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [levels]);

  const startRevising = () => navigate('/onboarding', { state: { level: selectedLevel } });

  return (
    <div>
      <div className="landing-hero-wrap">
        <LandingBlobs />
        <div className="landing-hero">
        <h1>
          Free. Fast.<br />
          <span style={{ color: 'var(--accent-color)' }}>No paywalls.</span>
        </h1>
        <p className="subtitle">
          Practice authentic past-paper questions on an exam-style canvas, get near-instant
          mark-scheme feedback, and let Acexam track exactly where you're losing marks - 100% free.
        </p>

        <div className="level-tabs" role="tablist" aria-label="Level">
          {levels.map(lvl => (
            <button
              key={lvl}
              role="tab"
              aria-selected={selectedLevel === lvl}
              className={`level-tab${selectedLevel === lvl ? ' active' : ''}`}
              onClick={() => setSelectedLevel(lvl)}
            >
              {lvl}
            </button>
          ))}
        </div>

        <div>
          <div className="landing-cta-note" style={{ marginBottom: '0.75rem' }}>
            Specific support for the following exam boards:
          </div>
          <div className="landing-board-row" aria-hidden="true">
            {boards.map(b => <span key={b}>{b}</span>)}
          </div>
        </div>

        {HOMEPAGE_ONLY ? (
          <p className="landing-cta-note landing-coming-soon-note">Sign-ups open soon — check back shortly.</p>
        ) : (
          <>
            <button
              onClick={startRevising}
              className="btn btn-primary btn-cta-animated"
              style={{ padding: '0.9rem 2.5rem', fontSize: '1.1rem', borderRadius: '999px' }}
            >
              Start revising free
            </button>
            <p className="landing-cta-note">No credit card required.</p>
          </>
        )}
        </div>
      </div>

      <div className="landing-features">
        <div className="card landing-feature-card">
          <span className="hero-stat-icon" aria-hidden="true"><Sparkles size={20} /></span>
          <h3 style={{ margin: '0.75rem 0 0.25rem' }}>Real past papers</h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', margin: 0 }}>
            AQA, Edexcel and OCR, marked exactly like the exam.
          </p>
        </div>
        <div className="card landing-feature-card">
          <span className="hero-stat-icon" aria-hidden="true"><Target size={20} /></span>
          <h3 style={{ margin: '0.75rem 0 0.25rem' }}>Adaptive practice</h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', margin: 0 }}>
            Targets your weakest topics and active misconceptions.
          </p>
        </div>
        <div className="card landing-feature-card">
          <span className="hero-stat-icon" aria-hidden="true"><TrendingUp size={20} /></span>
          <h3 style={{ margin: '0.75rem 0 0.25rem' }}>Mastery tracking</h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', margin: 0 }}>
            Watch topic and command-word scores climb over time.
          </p>
        </div>
      </div>

      <div className="landing-how">
        <h2 className="landing-section-heading">How it works</h2>
        <p className="landing-section-subheading">Three steps between you and knowing exactly what to revise next.</p>
        <div className="landing-how-steps">
          <div className="landing-how-step">
            <span className="landing-how-step-number" aria-hidden="true">1</span>
            <span className="hero-stat-icon" aria-hidden="true"><BookOpen size={20} /></span>
            <h3>Pick your subject</h3>
            <p>Tell us your board, level and subject - we surface real past-paper questions, not knock-offs.</p>
          </div>
          <div className="landing-how-step">
            <span className="landing-how-step-number" aria-hidden="true">2</span>
            <span className="hero-stat-icon" aria-hidden="true"><PenLine size={20} /></span>
            <h3>Answer like it's exam day</h3>
            <p>Type, draw, or select your answer on an exam-style canvas - multi-part questions and diagrams included.</p>
          </div>
          <div className="landing-how-step">
            <span className="landing-how-step-number" aria-hidden="true">3</span>
            <span className="hero-stat-icon" aria-hidden="true"><Zap size={20} /></span>
            <h3>Get instant feedback</h3>
            <p>See exactly which marking points you hit and missed, marked against the paper's own mark scheme - in seconds, not days.</p>
          </div>
        </div>
      </div>

      <div className="landing-why">
        <h2 className="landing-section-heading">Revising alone vs. revising with Acexam</h2>
        <div className="landing-compare">
          <div className="landing-compare-col landing-compare-without">
            <h3>Revising alone</h3>
            <ul>
              <li><X size={16} aria-hidden="true" /> Waiting days for a mock to come back marked</li>
              <li><X size={16} aria-hidden="true" /> No idea which mark-scheme point you missed</li>
              <li><X size={16} aria-hidden="true" /> Re-practicing topics you've already nailed</li>
            </ul>
          </div>
          <div className="landing-compare-col landing-compare-with">
            <h3>With Acexam</h3>
            <ul>
              <li><Check size={16} aria-hidden="true" /> Mark-scheme feedback in seconds</li>
              <li><Check size={16} aria-hidden="true" /> See exactly which point cost you the mark</li>
              <li><Check size={16} aria-hidden="true" /> An adaptive queue that targets your weak topics</li>
            </ul>
          </div>
        </div>
      </div>

      <div className="landing-faq">
        <h2 className="landing-section-heading">Questions, answered</h2>
        <details className="landing-faq-item">
          <summary>Is Acexam really free?</summary>
          <p>Yes. Every past paper, every AI-marked answer, and your full progress tracking are free - no paywalled tiers, no credit card.</p>
        </details>
        <details className="landing-faq-item">
          <summary>Which exam boards and levels are covered?</summary>
          <p>AQA, Edexcel and OCR, at GCSE and A-Level, with more papers and subjects being added over time.</p>
        </details>
        <details className="landing-faq-item">
          <summary>How does the AI marking actually work?</summary>
          <p>Short-answer questions are marked against rules compiled directly from the real mark scheme. Longer answers are marked by AI against that same paper's mark scheme, so feedback is grounded in what examiners actually award marks for - not a generic guess.</p>
        </details>
        <details className="landing-faq-item">
          <summary>Is my progress saved?</summary>
          <p>Yes - every attempt updates your topic and command-word mastery, so your next session picks up exactly where you left off.</p>
        </details>
      </div>

      {!HOMEPAGE_ONLY && (
        <div className="landing-final-cta">
          <span className="hero-stat-icon" aria-hidden="true"><ShieldCheck size={22} /></span>
          <h2>Stop guessing what you got wrong.</h2>
          <p>Start practicing real past papers and see exactly where your marks are going.</p>
          <button
            onClick={startRevising}
            className="btn btn-primary btn-cta-animated"
            style={{ padding: '0.9rem 2.5rem', fontSize: '1.1rem', borderRadius: '999px' }}
          >
            Start revising free
          </button>
        </div>
      )}

      <div className="sidebar-legal-links" style={{ padding: '1.5rem 0 2.5rem' }}>
        <Link to="/terms">Terms &amp; Conditions</Link>
        <span aria-hidden="true">·</span>
        <Link to="/privacy">Privacy Policy</Link>
      </div>
    </div>
  );
}
