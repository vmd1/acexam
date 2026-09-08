import { BrowserRouter as Router, Routes, Route, Link, Navigate, useNavigate } from 'react-router-dom';
import { useAuth } from './AuthContext';
import React, { useState, useEffect } from 'react';
import api, { extractErrorMessage } from './api';
import PracticeSetup from './PracticeSetup';
import SubjectPractice from './SubjectPractice';
import ExamQuestionsSetup from './ExamQuestionsSetup';
import CustomPapers from './CustomPapers';
import AttemptHistory from './AttemptHistory';
import PracticeSession from './PracticeSession';
import AdminManageSubjects from './AdminManageSubjects';
import AdminSubjectDetail from './AdminSubjectDetail';
import AnalyticsView from './AnalyticsView';
import ManageAccount from './ManageAccount';
import Onboarding from './Onboarding';
import Landing from './Landing';
import { TermsPage, PrivacyPage } from './Legal';
import NavDrawer from './NavDrawer';
import Sidebar, { BottomTabBar } from './Sidebar';
import Spinner from './Spinner';
import { GraduationCap, Sun, Moon, Menu, AlertCircle } from 'lucide-react';

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
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <>
      <header className="topbar">
        <div className="topbar-left">
          <button
            onClick={() => setDrawerOpen(true)}
            className="hamburger-btn"
            aria-label="Open menu"
            aria-expanded={drawerOpen}
          >
            <Menu size={20} aria-hidden="true" />
          </button>
          <Link to="/" className="topbar-brand">
            <GraduationCap size={22} aria-hidden="true" />
            Acexam
          </Link>
          <Link to="/login" className="btn btn-outline">Log in</Link>
          <Link to="/onboarding" className="btn btn-primary">Sign up free</Link>
        </div>
        <div className="topbar-actions">
          <button
            onClick={toggleTheme}
            className="theme-toggle"
            aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
          >
            {theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}
          </button>
        </div>
      </header>
      {drawerOpen && (
        <NavDrawer onClose={() => setDrawerOpen(false)} theme={theme} onToggleTheme={toggleTheme} />
      )}
    </>
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
    <div className={`app-container${user && collapsed ? ' sidebar-collapsed' : ''}${!user ? ' topbar-shell' : ''}`}>
      {user ? (
        <Sidebar theme={theme} onToggleTheme={toggleTheme} collapsed={collapsed} onToggleCollapsed={toggleCollapsed} />
      ) : (
        <TopBar theme={theme} toggleTheme={toggleTheme} />
      )}

      <main className={user ? '' : 'no-sidebar'} style={user ? undefined : { marginLeft: 'auto' }}>
        <Routes>
          <Route path="/" element={user ? <Navigate to="/app" replace /> : <Landing />} />
          <Route path="/login" element={<Login />} />
          <Route path="/terms" element={<TermsPage />} />
          <Route path="/privacy" element={<PrivacyPage />} />
          <Route path="/register" element={<Navigate to="/onboarding" replace />} />
          <Route path="/onboarding" element={<Onboarding />} />
          <Route path="/app" element={<RequireSubjects><PracticeSetup /></RequireSubjects>} />
          <Route path="/app/history" element={<RequireSubjects><AttemptHistory /></RequireSubjects>} />
          <Route path="/app/subject/:subjectKey" element={<RequireSubjects><SubjectPractice /></RequireSubjects>} />
          <Route path="/app/subject/:subjectKey/exam-questions" element={<RequireSubjects><ExamQuestionsSetup /></RequireSubjects>} />
          <Route path="/app/subject/:subjectKey/past-papers" element={<RequireSubjects><CustomPapers /></RequireSubjects>} />
          <Route path="/app/subject/:subjectKey/analytics" element={<RequireSubjects><AnalyticsView /></RequireSubjects>} />
          <Route path="/app/session" element={<RequireSubjects><PracticeSession /></RequireSubjects>} />
          <Route path="/analytics" element={<Navigate to="/app" replace />} />
          <Route path="/account" element={<ProtectedRoute><ManageAccount /></ProtectedRoute>} />
          <Route path="/admin/subjects" element={<AdminRoute><AdminManageSubjects /></AdminRoute>} />
          <Route path="/admin/subjects/:id" element={<AdminRoute><AdminSubjectDetail /></AdminRoute>} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>

      {user && <BottomTabBar />}

      <a
        href="https://vmd1.dev"
        target="_blank"
        rel="noopener noreferrer"
        className="corner-logo-link"
        aria-label="vmd1.dev"
      >
        <img src="https://cdn.848226.xyz/v1/vmd1/media/logo/logo.svg" alt="" className="corner-logo" />
      </a>
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

function NotFound() {
  return <h1 className="text-center" style={{ marginTop: '5rem' }}>404 — page not found</h1>;
}

export default App;
