import React, { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from './AuthContext';
import api, { extractErrorMessage } from './api';
import Spinner from './Spinner';
import { iconForSubject } from './subjectIcons';
import { AlertCircle, Check, GraduationCap } from 'lucide-react';

interface SupportedCombo {
  level: string;
  exam_board: string;
  subject: string;
}

interface Qualification {
  exam_board: string;
  level: string;
  subject: string;
  tiers: string[];
}

interface StagedSubject {
  level: string;
  exam_board: string;
  subject: string;
  tier?: string | null;
}

// Rebuilt as a slideshow: Welcome -> name -> level -> subjects -> exam
// board(/tier) per subject -> (guest only) create account. A logged-in
// user with zero subjects (redirected here by RequireSubjects) skips the
// name/account-creation slides entirely and starts at the level slide.
export default function Onboarding() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, login, refreshSubjects } = useAuth();
  const isGuest = !user;

  const [loading, setLoading] = useState(true);
  const [combos, setCombos] = useState<SupportedCombo[]>([]);
  const [qualifications, setQualifications] = useState<Qualification[]>([]);

  const [step, setStep] = useState(0);
  const [name, setName] = useState('');
  const [level, setLevel] = useState((location.state as any)?.level || '');
  const [selectedSubjects, setSelectedSubjects] = useState<string[]>([]);
  const [boardChoice, setBoardChoice] = useState<Record<string, string>>({});
  const [tierChoice, setTierChoice] = useState<Record<string, string>>({});
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const topicsRes = await api.get('/exams/topics');
        const qualificationsRes = await api.get('/exams/qualifications');
        setQualifications(qualificationsRes.data);

        const seen = new Set<string>();
        const supported: SupportedCombo[] = [];
        (topicsRes.data as any[]).forEach(t => {
          const key = `${t.level}::${t.exam_board}::${t.subject}`;
          if (!seen.has(key)) {
            seen.add(key);
            supported.push({ level: t.level, exam_board: t.exam_board, subject: t.subject });
          }
        });
        setCombos(supported);
      } catch (err) {
        console.error('Failed to load onboarding data', err);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const levelOptions = useMemo(() => Array.from(new Set(combos.map(c => c.level))), [combos]);
  useEffect(() => {
    if (!level && levelOptions.length > 0) setLevel(levelOptions[0]);
  }, [level, levelOptions]);

  const subjectOptions = useMemo(
    () => Array.from(new Set(combos.filter(c => c.level === level).map(c => c.subject))),
    [combos, level]
  );

  const boardsForSubject = (subjectName: string) =>
    Array.from(new Set(combos.filter(c => c.level === level && c.subject === subjectName).map(c => c.exam_board)));

  const tiersFor = (board: string, subjectName: string): string[] => {
    const q = qualifications.find(q => q.level === level && q.exam_board === board && q.subject === subjectName);
    return q?.tiers ?? [];
  };

  const toggleSubject = (subjectName: string) => {
    setSelectedSubjects(prev =>
      prev.includes(subjectName) ? prev.filter(s => s !== subjectName) : [...prev, subjectName]
    );
  };

  // Slide order differs for guest vs authenticated-no-subjects; represented
  // as named steps so "back"/"next" and skip logic stay readable.
  const guestSteps = ['welcome', 'name', 'level', 'subjects', 'boards', 'account'] as const;
  const authedSteps = ['level', 'subjects', 'boards'] as const;
  const steps = isGuest ? guestSteps : authedSteps;
  const currentStepName = steps[step];

  const goNext = () => setStep(s => Math.min(s + 1, steps.length - 1));
  const goBack = () => setStep(s => Math.max(s - 1, 0));

  const stagedSubjects: StagedSubject[] = selectedSubjects.map(subjectName => {
    const chosenBoard = boardChoice[subjectName] || boardsForSubject(subjectName)[0];
    // Mirrors exam_board's own fallback above: the tier picker visually
    // shows its first option as selected whenever the student hasn't
    // clicked one (see the tier button below), so the saved subject must
    // fall back the same way - otherwise a student who never explicitly
    // clicked a tier button (because one already looked selected) had
    // their tier silently saved as null despite the UI showing "Higher".
    return {
      level,
      subject: subjectName,
      exam_board: chosenBoard,
      tier: tierChoice[subjectName] || tiersFor(chosenBoard, subjectName)[0] || null,
    };
  });

  const boardsSlideValid = selectedSubjects.every(s => !!(boardChoice[s] || boardsForSubject(s)[0]));

  const finishAuthed = async () => {
    setSubmitting(true);
    setError('');
    try {
      await Promise.all(stagedSubjects.map(s => api.post('/auth/me/subjects', s)));
      await refreshSubjects();
      navigate('/app');
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to save your subjects'));
    } finally {
      setSubmitting(false);
    }
  };

  const finishGuest = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await api.post('/auth/register', { display_name: name, email, password });
      await api.post('/auth/login', { email, password });
      const userRes = await api.get('/auth/me');
      await login('cookie-managed', userRes.data);
      await Promise.all(stagedSubjects.map(s => api.post('/auth/me/subjects', s)));
      await refreshSubjects();
      navigate('/app');
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Registration failed'));
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return <Spinner label="Setting things up…" />;
  }

  return (
    <div className="container onboarding-container" style={{ margin: '2.5rem auto 4rem', maxWidth: '600px' }}>
      <div style={{ display: 'flex', justifyContent: 'center', gap: '0.4rem', marginBottom: '2rem' }}>
        {steps.map((s, i) => (
          <span
            key={s}
            className={`onboarding-dot${i === step ? ' active' : ''}`}
            style={{
              width: 8, height: 8, borderRadius: '50%',
              background: i <= step ? 'var(--accent-color)' : 'var(--border)',
            }}
          />
        ))}
      </div>

      {error && (
        <div className="banner banner-danger" role="alert" style={{ marginBottom: '1rem' }}>
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      )}

      <div key={currentStepName} className="onboarding-step">
      {currentStepName === 'welcome' && (
        <div className="card text-center" style={{ padding: '3rem 2rem' }}>
          <GraduationCap size={40} style={{ color: 'var(--accent-color)', marginBottom: '1rem' }} aria-hidden="true" />
          <h1 style={{ marginBottom: '0.5rem' }}>Welcome to Acexam</h1>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
            Free, AI-marked past-paper practice. Let's get you set up - it only takes a minute.
          </p>
          <button onClick={goNext} className="btn btn-primary" style={{ width: '100%' }}>Get started</button>
        </div>
      )}

      {currentStepName === 'name' && (
        <div className="card">
          <h2 style={{ marginBottom: '0.5rem' }}>What's your name?</h2>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>So we know what to call you.</p>
          <div className="form-group">
            <input
              autoFocus
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              className="form-control"
              placeholder="Jane Doe"
            />
          </div>
          <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.5rem' }}>
            <button onClick={goBack} className="btn btn-outline">Back</button>
            <button onClick={goNext} className="btn btn-primary" disabled={!name.trim()} style={{ flex: 1 }}>Continue</button>
          </div>
        </div>
      )}

      {currentStepName === 'level' && (
        <div className="card">
          <h2 style={{ marginBottom: '0.5rem' }}>{isGuest && name ? `Hi ${name}, what` : 'What'} level do you study?</h2>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>You can add more later.</p>
          <div className="level-tabs" role="tablist" aria-label="Level">
            {levelOptions.map(lvl => (
              <button
                key={lvl}
                role="tab"
                aria-selected={level === lvl}
                className={`level-tab${level === lvl ? ' active' : ''}`}
                onClick={() => setLevel(lvl)}
              >
                {lvl}
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.5rem' }}>
            {isGuest && <button onClick={goBack} className="btn btn-outline">Back</button>}
            <button onClick={goNext} className="btn btn-primary" disabled={!level} style={{ flex: 1 }}>Continue</button>
          </div>
        </div>
      )}

      {currentStepName === 'subjects' && (
        <div className="card">
          <h2 style={{ marginBottom: '0.5rem' }}>Pick your subjects</h2>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>Select every {level} subject you're taking.</p>
          <div className="subject-grid">
            {subjectOptions.map(subjectName => {
              const Icon = iconForSubject(subjectName);
              const selected = selectedSubjects.includes(subjectName);
              return (
                <button
                  key={subjectName}
                  className={`subject-tile${selected ? ' selected' : ''}`}
                  onClick={() => toggleSubject(subjectName)}
                  aria-pressed={selected}
                >
                  <Icon size={18} aria-hidden="true" />
                  <span>{subjectName}</span>
                  {selected && <Check size={14} aria-hidden="true" style={{ marginLeft: 'auto' }} />}
                </button>
              );
            })}
          </div>
          <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.5rem' }}>
            <button onClick={goBack} className="btn btn-outline">Back</button>
            <button onClick={goNext} className="btn btn-primary" disabled={selectedSubjects.length === 0} style={{ flex: 1 }}>
              Continue
            </button>
          </div>
        </div>
      )}

      {currentStepName === 'boards' && (
        <div className="card">
          <h2 style={{ marginBottom: '0.5rem' }}>Choose your exam board</h2>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>For each subject, pick the board (and tier, if it applies).</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
            {selectedSubjects.map(subjectName => {
              const boardOptions = boardsForSubject(subjectName);
              const chosenBoard = boardChoice[subjectName] || boardOptions[0];
              const tierOptions = chosenBoard ? tiersFor(chosenBoard, subjectName) : [];
              return (
                <div key={subjectName}>
                  <strong>{subjectName}</strong>
                  {boardOptions.length > 1 ? (
                    <div className="board-picker-options" style={{ marginTop: '0.5rem' }}>
                      {boardOptions.map(board => (
                        <button
                          key={board}
                          className={`board-picker-btn${chosenBoard === board ? ' selected' : ''}`}
                          onClick={() => setBoardChoice(prev => ({ ...prev, [subjectName]: board }))}
                        >
                          {board}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', margin: '0.25rem 0 0' }}>{boardOptions[0]}</p>
                  )}
                  {tierOptions.length > 0 && (
                    <div className="board-picker-options" style={{ marginTop: '0.5rem' }}>
                      {tierOptions.map(tier => (
                        <button
                          key={tier}
                          className={`board-picker-btn${(tierChoice[subjectName] || tierOptions[0]) === tier ? ' selected' : ''}`}
                          onClick={() => setTierChoice(prev => ({ ...prev, [subjectName]: tier }))}
                        >
                          {tier}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.5rem' }}>
            <button onClick={goBack} className="btn btn-outline">Back</button>
            {isGuest ? (
              <button onClick={goNext} className="btn btn-primary" disabled={!boardsSlideValid} style={{ flex: 1 }}>
                Continue
              </button>
            ) : (
              <button onClick={finishAuthed} className="btn btn-primary" disabled={!boardsSlideValid || submitting} style={{ flex: 1 }}>
                {submitting ? 'Saving…' : 'Continue to practice'}
              </button>
            )}
          </div>
        </div>
      )}

      {currentStepName === 'account' && (
        <div className="card">
          <h2 style={{ marginBottom: '0.5rem' }}>Create your free account</h2>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>Last step - this saves your progress.</p>
          <form onSubmit={finishGuest}>
            <div className="form-group">
              <label htmlFor="onboarding-email">Email address</label>
              <input id="onboarding-email" type="email" value={email} onChange={e => setEmail(e.target.value)} className="form-control" placeholder="you@example.com" required />
            </div>
            <div className="form-group">
              <label htmlFor="onboarding-password">Password</label>
              <input id="onboarding-password" type="password" value={password} onChange={e => setPassword(e.target.value)} className="form-control" placeholder="••••••••" required minLength={8} />
            </div>
            <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.5rem' }}>
              <button type="button" onClick={goBack} className="btn btn-outline">Back</button>
              <button type="submit" className="btn btn-primary" disabled={submitting} style={{ flex: 1 }}>
                {submitting ? 'Creating account…' : 'Create account & start practicing'}
              </button>
            </div>
          </form>
        </div>
      )}
      </div>
    </div>
  );
}
