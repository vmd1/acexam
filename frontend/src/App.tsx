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

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="container text-center" style={{ marginTop: '5rem' }}>Loading...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

// Like ProtectedRoute, but also sends first-time users (no subjects added
// yet) to the onboarding flow before they can reach practice/analytics.
function RequireSubjects({ children }: { children: React.ReactNode }) {
  const { user, loading, hasSubjects } = useAuth();
  if (loading) return <div className="container text-center" style={{ marginTop: '5rem' }}>Loading...</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (hasSubjects === null) return <div className="container text-center" style={{ marginTop: '5rem' }}>Loading...</div>;
  if (hasSubjects === false) return <Navigate to="/onboarding" replace />;
  return <>{children}</>;
}

function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="container text-center" style={{ marginTop: '5rem' }}>Loading...</div>;
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

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  return (
    <button
      onClick={() => setTheme(prev => (prev === 'light' ? 'dark' : 'light'))}
      className="theme-toggle"
      title={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
      aria-label="Toggle color theme"
    >
      {theme === 'light' ? '🌙' : '☀️'}
    </button>
  );
}

function App() {
  const { user, logout } = useAuth();

  return (
    <Router>
      <div className="app-container">
        <nav>
          <Link to="/" className="nav-brand">Acexam</Link>
          <ul>
            <li><Link to="/app">Practice</Link></li>
            {user && <li><Link to="/analytics">Analytics</Link></li>}
            {user?.is_admin && <li><Link to="/admin/ingestion">Upload PDF</Link></li>}
            {user?.is_admin && <li><Link to="/admin/review">Admin Console</Link></li>}
          </ul>
          <div className="nav-auth-buttons">
            <ThemeToggle />
            {user ? (
              <>
                <Link to="/account" style={{ color: 'var(--text-secondary)', alignSelf: 'center', fontWeight: 600 }}>
                  {user.display_name}
                </Link>
                <button onClick={logout} className="btn btn-outline" style={{ padding: '0.4rem 0.9rem' }}>
                  Logout
                </button>
              </>
            ) : (
              <>
                <Link to="/login" className="btn btn-outline">Log in</Link>
                <Link to="/register" className="btn btn-primary">Sign up free</Link>
              </>
            )}
          </div>
        </nav>
        
        <main>
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
      </div>
    </Router>
  );
}

function Home() {
  return (
    <div className="hero">
      <h1>AI-Powered Revision,<br /><span style={{ color: 'var(--accent-color)' }}>100% Free Forever.</span></h1>
      <p className="subtitle">
        Authentic past-paper canvas, near-instant mark scheme feedback, and personalized weakness tracking to crush your GCSEs and A-Levels.
      </p>
      <div style={{ display: 'flex', gap: '1rem' }}>
        <Link to="/register" className="btn btn-primary" style={{ padding: '0.8rem 2rem', fontSize: '1.1rem' }}>Start Practicing</Link>
        <Link to="/app" className="btn btn-outline" style={{ padding: '0.8rem 2rem', fontSize: '1.1rem' }}>Browse Papers</Link>
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
      {error && <div style={{ color: '#ef4444', marginBottom: '1rem', textAlign: 'center' }}>{error}</div>}
      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label>Email address</label>
          <input type="email" value={email} onChange={e => setEmail(e.target.value)} className="form-control" placeholder="you@example.com" required />
        </div>
        <div className="form-group">
          <label>Password</label>
          <input type="password" value={password} onChange={e => setPassword(e.target.value)} className="form-control" placeholder="••••••••" required />
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
      {error && <div style={{ color: '#ef4444', marginBottom: '1rem', textAlign: 'center' }}>{error}</div>}
      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label>Full name</label>
          <input type="text" value={name} onChange={e => setName(e.target.value)} className="form-control" placeholder="Jane Doe" required />
        </div>
        <div className="form-group">
          <label>Email address</label>
          <input type="email" value={email} onChange={e => setEmail(e.target.value)} className="form-control" placeholder="you@example.com" required />
        </div>
        <div className="form-group">
          <label>Password</label>
          <input type="password" value={password} onChange={e => setPassword(e.target.value)} className="form-control" placeholder="••••••••" required />
        </div>
        <button type="submit" className="btn btn-primary" style={{ width: '100%', marginTop: '1rem' }}>Sign up</button>
      </form>
    </div>
  );
}

function NotFound() {
  return <h1 className="text-center" style={{ marginTop: '5rem' }}>404 - Not Found</h1>;
}

export default App;
