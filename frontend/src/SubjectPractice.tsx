import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api, { extractErrorMessage } from './api';
import Spinner from './Spinner';
import Modal from './Modal';
import { parseSubjectKey } from './PracticeSetup';
import { FileEdit, ChevronRight, Sparkles, LineChart, Pencil, Target, Trophy } from 'lucide-react';

interface UserSubject {
  id: string;
  exam_board: string;
  level: string;
  subject: string;
  tier: string | null;
  target_grade: string | null;
  latest_test_grade: string | null;
  show_latest_test_grade: boolean;
}

export interface AttemptSummary {
  id: string;
  mode: 'custom' | 'exam_questions' | 'adaptive';
  title: string | null;
  subject: string | null;
  exam_board: string | null;
  level: string | null;
  started_at: string;
  completed_at: string | null;
  question_count: number;
  marks_earned: number;
  marks_possible: number;
}

export function AttemptRow({ attempt }: { attempt: AttemptSummary }) {
  const navigate = useNavigate();
  const pct = attempt.marks_possible > 0 ? Math.round((attempt.marks_earned / attempt.marks_possible) * 100) : null;
  return (
    <button
      className="card"
      style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%', textAlign: 'left', cursor: 'pointer' }}
      onClick={() => navigate('/app/session', { state: { mode: 'review', attemptId: attempt.id } })}
    >
      <div>
        <div style={{ fontWeight: 700 }}>{attempt.title || 'Practice session'}</div>
        <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
          {new Date(attempt.started_at).toLocaleDateString()} · {attempt.question_count} question{attempt.question_count === 1 ? '' : 's'}
          {pct !== null ? ` · ${attempt.marks_earned}/${attempt.marks_possible} marks (${pct}%)` : ''}
        </div>
      </div>
      <ChevronRight size={18} aria-hidden="true" style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
    </button>
  );
}

export default function SubjectPractice() {
  const { subjectKey = '' } = useParams();
  const navigate = useNavigate();
  const { level, exam_board, subject } = parseSubjectKey(decodeURIComponent(subjectKey));

  const [papers, setPapers] = useState<AttemptSummary[]>([]);
  const [sessions, setSessions] = useState<AttemptSummary[]>([]);
  const [userSubject, setUserSubject] = useState<UserSubject | null>(null);
  const [predictedGrade, setPredictedGrade] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [editOpen, setEditOpen] = useState(false);
  const [targetGradeDraft, setTargetGradeDraft] = useState('');
  const [latestTestGradeDraft, setLatestTestGradeDraft] = useState('');
  const [showLatestTestDraft, setShowLatestTestDraft] = useState(false);
  const [savingGrades, setSavingGrades] = useState(false);
  const [gradeError, setGradeError] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const [papersRes, sessionsRes, subjectsRes, profileRes] = await Promise.all([
        api.get('/generate/attempts', { params: { mode: 'custom', subject } }),
        api.get('/generate/attempts', { params: { mode: 'exam_questions', subject } }),
        api.get('/auth/me/subjects'),
        api.get('/analytics/profile', { params: { subject, exam_board, level } }),
      ]);
      setPapers(papersRes.data.slice(0, 5));
      setSessions(sessionsRes.data.slice(0, 5));
      const match: UserSubject | undefined = subjectsRes.data.find((s: UserSubject) =>
        s.subject === subject && s.exam_board === exam_board && s.level === level
      );
      setUserSubject(match ?? null);
      setPredictedGrade(profileRes.data?.predicted_grade ?? null);
    } catch (err) {
      console.error('Failed to load subject practice history', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subject, exam_board, level]);

  const openEditGrades = () => {
    if (!userSubject) return;
    setTargetGradeDraft(userSubject.target_grade ?? '');
    setLatestTestGradeDraft(userSubject.latest_test_grade ?? '');
    setShowLatestTestDraft(userSubject.show_latest_test_grade);
    setGradeError('');
    setEditOpen(true);
  };

  const saveGrades = async (e: FormEvent) => {
    e.preventDefault();
    if (!userSubject) return;
    setSavingGrades(true);
    setGradeError('');
    try {
      const res = await api.patch(`/auth/me/subjects/${userSubject.id}`, {
        target_grade: targetGradeDraft.trim() || null,
        latest_test_grade: latestTestGradeDraft.trim() || null,
        show_latest_test_grade: showLatestTestDraft,
      });
      setUserSubject(res.data);
      setEditOpen(false);
    } catch (err) {
      setGradeError(extractErrorMessage(err, 'Failed to save grades'));
    } finally {
      setSavingGrades(false);
    }
  };

  if (loading) {
    return <Spinner label={`Loading ${subject}…`} />;
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem', maxWidth: '900px' }}>
      <h1 style={{ marginBottom: '0.25rem' }}>{subject}</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '1.25rem' }}>{level} {exam_board}{userSubject?.tier ? ` · ${userSubject.tier}` : ''}</p>

      {userSubject && (
        <div style={{ position: 'relative', marginBottom: '2rem' }}>
          <button
            className="btn btn-outline"
            onClick={openEditGrades}
            aria-label="Edit grades"
            style={{ position: 'absolute', top: '-0.6rem', right: '-0.6rem', padding: '0.45rem', borderRadius: '50%', zIndex: 1 }}
          >
            <Pencil size={14} aria-hidden="true" />
          </button>

          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
            gap: '1.25rem',
          }}>
            <div className="card">
              <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Target grade</div>
              <div style={{ fontSize: '2.2rem', fontWeight: 800, color: 'var(--warning)', fontFamily: 'var(--font-heading)' }}>
                {userSubject.target_grade || '—'}
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem', display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                <Trophy size={12} aria-hidden="true" /> What you're aiming for
              </div>
            </div>

            <div className="card">
              <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Currently at (Acexam)</div>
              <div style={{ fontSize: '2.2rem', fontWeight: 800, color: 'var(--accent-color)', fontFamily: 'var(--font-heading)' }}>
                {predictedGrade || 'Calibrating'}
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem', display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                <Target size={12} aria-hidden="true" /> Based on 9-1 grade boundaries
              </div>
            </div>

            {userSubject.show_latest_test_grade && userSubject.latest_test_grade && (
              <div className="card">
                <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Latest test grade</div>
                <div style={{ fontSize: '2.2rem', fontWeight: 800, color: 'var(--success)', fontFamily: 'var(--font-heading)' }}>
                  {userSubject.latest_test_grade}
                </div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>Self-reported</div>
              </div>
            )}
          </div>
        </div>
      )}

      {editOpen && userSubject && (
        <Modal title="Edit grades" onClose={() => setEditOpen(false)}>
          <form onSubmit={saveGrades}>
            {gradeError && (
              <div className="banner banner-danger" role="alert" style={{ marginBottom: '1.5rem' }}>
                <span>{gradeError}</span>
              </div>
            )}

            <div className="form-group" style={{ marginBottom: '1rem' }}>
              <label htmlFor="target-grade">Target grade</label>
              <input
                id="target-grade"
                type="text"
                className="form-control"
                placeholder="e.g. 7"
                value={targetGradeDraft}
                onChange={e => setTargetGradeDraft(e.target.value)}
              />
            </div>

            <div className="form-group" style={{ marginBottom: '0.5rem' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={showLatestTestDraft}
                  onChange={e => setShowLatestTestDraft(e.target.checked)}
                />
                Show my most recent test grade on this page
              </label>
            </div>

            {showLatestTestDraft && (
              <div className="form-group" style={{ marginBottom: '1.5rem' }}>
                <label htmlFor="latest-test-grade">Latest test grade</label>
                <input
                  id="latest-test-grade"
                  type="text"
                  className="form-control"
                  placeholder="e.g. 6 (mock, Jan 2026)"
                  value={latestTestGradeDraft}
                  onChange={e => setLatestTestGradeDraft(e.target.value)}
                />
              </div>
            )}

            <button
              type="submit"
              className="btn btn-primary"
              disabled={savingGrades}
              style={{ width: '100%', padding: '0.65rem' }}
            >
              {savingGrades ? 'Saving…' : 'Save'}
            </button>
          </form>
        </Modal>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1rem', marginBottom: '2.5rem' }}>
        <button
          className="card"
          style={{ textAlign: 'left', cursor: 'pointer' }}
          onClick={() => navigate(`/app/subject/${subjectKey}/past-papers`)}
        >
          <span className="hero-stat-icon" aria-hidden="true" style={{ marginBottom: '0.75rem' }}><FileEdit size={20} /></span>
          <h3 style={{ marginBottom: '0.25rem' }}>Custom papers</h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', margin: 0 }}>
            Generate a full timed mock paper. Saved automatically so you can come back and review it.
          </p>
        </button>
        <button
          className="card"
          style={{ textAlign: 'left', cursor: 'pointer' }}
          onClick={() => navigate(`/app/subject/${subjectKey}/exam-questions`)}
        >
          <span className="hero-stat-icon" aria-hidden="true" style={{ marginBottom: '0.75rem' }}><Sparkles size={20} /></span>
          <h3 style={{ marginBottom: '0.25rem' }}>Exam questions</h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', margin: 0 }}>
            Endless practice - random, or targeted at your weak topics. Quit whenever you like.
          </p>
        </button>
        <button
          className="card"
          style={{ textAlign: 'left', cursor: 'pointer' }}
          onClick={() => navigate(`/app/subject/${subjectKey}/analytics`)}
        >
          <span className="hero-stat-icon" aria-hidden="true" style={{ marginBottom: '0.75rem' }}><LineChart size={20} /></span>
          <h3 style={{ marginBottom: '0.25rem' }}>Analytics</h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', margin: 0 }}>
            Topic mastery, memory decay, and misconceptions we're tracking for {subject}.
          </p>
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '2rem' }}>
        <div>
          <h3 style={{ marginBottom: '0.75rem' }}>Your saved papers</h3>
          {papers.length === 0 ? (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>No papers generated yet.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {papers.map(a => <AttemptRow key={a.id} attempt={a} />)}
            </div>
          )}
        </div>
        <div>
          <h3 style={{ marginBottom: '0.75rem' }}>Previous sessions</h3>
          {sessions.length === 0 ? (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>No exam-question sessions yet.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {sessions.map(a => <AttemptRow key={a.id} attempt={a} />)}
            </div>
          )}
        </div>
      </div>

      <div style={{ textAlign: 'center', marginTop: '3rem', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
        Spotted an issue with this exam? <a href="mailto:acexam@vmd1.dev">Contact us</a>
      </div>
    </div>
  );
}
