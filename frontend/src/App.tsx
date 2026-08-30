import { BrowserRouter as Router, Routes, Route, Link, Navigate, useNavigate } from 'react-router-dom';
import { useAuth } from './AuthContext';
import React, { useState, useEffect } from 'react';
import api, { extractErrorMessage } from './api';
import AdminIngestion from './AdminIngestion';
import AdminReview from './AdminReview';
import PracticeSetup from './PracticeSetup';
import PracticeSession from './PracticeSession';
import AnalyticsView from './AnalyticsView';
import ManageAccount from './ManageAccount';
import Onboarding from './Onboarding';
import Sidebar, { BottomTabBar } from './Sidebar';
import Spinner from './Spinner';
import { GraduationCap, Sun, Moon, Sparkles, Target, TrendingUp, AlertCircle } from 'lucide-react';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <Spinner label="Checking your session…" />;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

// Like ProtectedRoute, but also sends first-time users (no subjects added
// yet) to the onboarding flow before they can reach practice/analytics.
function RequireSubjects({ children }: { children: React.ReactNode }) {
  const { user, loading, hasSubjects } = useAuth();
  if (loading) return <Spinner label="Checking your session…" />;
  if (!user) return <Navigate to="/login" replace />;
  if (hasSubjects === null) return <Spinner label="Loading your subjects…" />;
  if (hasSubjects === false) return <Navigate to="/onboarding" replace />;
  return <>{children}</>;
}

function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <Spinner label="Checking your session…" />;
  if (!user) return <Navigate to="/login" replace />;
  if (!user.is_admin) return <Navigate to="/app" replace />;
  return <>{children}</>;
}

type Theme = 'light' | 'dark';

function getInitialTheme(): Theme {
  const stored = localStorage.getItem('theme');
  if (stored === 'light' || stored === 'dark') return stored;
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

function useTheme() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  const toggleTheme = () => setTheme(prev => (prev === 'light' ? 'dark' : 'light'));
  return { theme, toggleTheme };
}

function TopBar({ theme, toggleTheme }: { theme: Theme; toggleTheme: () => void }) {
  return (
    <header className="topbar">
      <Link to="/" className="topbar-brand">
        <GraduationCap size={22} aria-hidden="true" />
        Acexam
      </Link>
      <div className="topbar-actions">
        <button
          onClick={toggleTheme}
          className="theme-toggle"
          aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
        >
          {theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}
        </button>
        <Link to="/login" className="btn btn-outline">Log in</Link>
        <Link to="/register" className="btn btn-primary">Sign up free</Link>
      </div>
    </header>
  );
}

function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('sidebarCollapsed') === 'true');

  useEffect(() => {
    localStorage.setItem('sidebarCollapsed', String(collapsed));
  }, [collapsed]);

  return { collapsed, toggleCollapsed: () => setCollapsed(prev => !prev) };
}

function AppShell() {
  const { user } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { collapsed, toggleCollapsed } = useSidebarCollapsed();

  return (
    <div className={`app-container${user && collapsed ? ' sidebar-collapsed' : ''}`}>
      {user ? (
        <Sidebar theme={theme} onToggleTheme={toggleTheme} collapsed={collapsed} onToggleCollapsed={toggleCollapsed} />
      ) : (
        <TopBar theme={theme} toggleTheme={toggleTheme} />
      )}

      <main className={user ? '' : 'no-sidebar'} style={user ? undefined : { marginLeft: 'auto' }}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/onboarding" element={<ProtectedRoute><Onboarding /></ProtectedRoute>} />
          <Route path="/app" element={<RequireSubjects><PracticeSetup /></RequireSubjects>} />
          <Route path="/app/session" element={<RequireSubjects><PracticeSession /></RequireSubjects>} />
          <Route path="/analytics" element={<RequireSubjects><AnalyticsView /></RequireSubjects>} />
          <Route path="/account" element={<ProtectedRoute><ManageAccount /></ProtectedRoute>} />
          <Route path="/admin/ingestion" element={<AdminRoute><AdminIngestion /></AdminRoute>} />
          <Route path="/admin/review" element={<AdminRoute><AdminReview /></AdminRoute>} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>

      {user && <BottomTabBar />}
    </div>
  );
}

function App() {
  return (
    <Router>
      <AppShell />
    </Router>
  );
}

function Home() {
  return (
    <div className="hero">
      <div className="hero-content">
        <h1>
          AI-powered revision,<br />
          <span style={{ color: 'var(--accent-color)' }}>100% free forever.</span>
        </h1>
        <p className="subtitle">
          Practice authentic past-paper questions on an exam-style canvas, get near-instant
          mark-scheme feedback, and let Acexam track exactly where you're losing marks.
        </p>
        <div className="hero-actions">
          <Link to="/register" className="btn btn-primary" style={{ padding: '0.8rem 2rem', fontSize: '1.05rem' }}>
            Start practicing
          </Link>
          <Link to="/app" className="btn btn-outline" style={{ padding: '0.8rem 2rem', fontSize: '1.05rem' }}>
            Browse papers
          </Link>
        </div>
      </div>

      <div className="hero-panel" aria-hidden="true">
        <div className="hero-stat">
          <span className="hero-stat-icon"><Sparkles size={20} /></span>
          <div>
            <div className="hero-stat-value">Real past papers</div>
            <div className="hero-stat-label">AQA, Edexcel and OCR, marked exactly like the exam</div>
          </div>
        </div>
        <div className="hero-stat">
          <span className="hero-stat-icon"><Target size={20} /></span>
          <div>
            <div className="hero-stat-value">Adaptive practice</div>
            <div className="hero-stat-label">Targets your weakest topics and active misconceptions</div>
          </div>
        </div>
        <div className="hero-stat">
          <span className="hero-stat-icon"><TrendingUp size={20} /></span>
          <div>
            <div className="hero-stat-value">Mastery tracking</div>
            <div className="hero-stat-label">Watch topic and command-word scores climb over time</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      await api.post('/auth/login', { email, password });
      const userRes = await api.get('/auth/me');
      await login('cookie-managed', userRes.data);
      navigate('/app');
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Invalid email or password'));
    }
  };

  return (
    <div className="auth-container card">
      <h2 className="text-center" style={{ marginBottom: '2rem' }}>Welcome back</h2>
      {error && (
        <div className="banner banner-danger" role="alert" style={{ marginBottom: '1rem' }}>
          <AlertCircle size={18} />
          <span>{error}</span>
        </div>
      )}
      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label htmlFor="login-email">Email address</label>
          <input id="login-email" type="email" value={email} onChange={e => setEmail(e.target.value)} className="form-control" placeholder="you@example.com" required />
        </div>
        <div className="form-group">
          <label htmlFor="login-password">Password</label>
          <input id="login-password" type="password" value={password} onChange={e => setPassword(e.target.value)} className="form-control" placeholder="••••••••" required />
        </div>
        <button type="submit" className="btn btn-primary" style={{ width: '100%', marginTop: '1rem' }}>Log in</button>
      </form>
    </div>
  );
}

function Register() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      await api.post('/auth/register', { display_name: name, email, password });
      await api.post('/auth/login', { email, password });
      const userRes = await api.get('/auth/me');
      await login('cookie-managed', userRes.data);
      navigate('/onboarding');
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Registration failed'));
    }
  };

  return (
    <div className="auth-container card">
      <h2 className="text-center" style={{ marginBottom: '2rem' }}>Create your free account</h2>
      {error && (
        <div className="banner banner-danger" role="alert" style={{ marginBottom: '1rem' }}>
          <AlertCircle size={18} />
          <span>{error}</span>
        </div>
      )}
      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label htmlFor="register-name">Full name</label>
          <input id="register-name" type="text" value={name} onChange={e => setName(e.target.value)} className="form-control" placeholder="Jane Doe" required />
        </div>
        <div className="form-group">
          <label htmlFor="register-email">Email address</label>
          <input id="register-email" type="email" value={email} onChange={e => setEmail(e.target.value)} className="form-control" placeholder="you@example.com" required />
        </div>
        <div className="form-group">
          <label htmlFor="register-password">Password</label>
          <input id="register-password" type="password" value={password} onChange={e => setPassword(e.target.value)} className="form-control" placeholder="••••••••" required />
        </div>
        <button type="submit" className="btn btn-primary" style={{ width: '100%', marginTop: '1rem' }}>Sign up</button>
      </form>
    </div>
  );
}

function NotFound() {
  return <h1 className="text-center" style={{ marginTop: '5rem' }}>404 — page not found</h1>;
}

export default App;
