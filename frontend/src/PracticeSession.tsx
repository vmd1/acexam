import { useState, useEffect, useMemo, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import api from './api';
import ExamCanvas from './ExamCanvas';
import type { Question } from './types';
import Spinner from './Spinner';
import EmptyState from './EmptyState';
import { subjectKey } from './PracticeSetup';
import { AlertTriangle, ChevronLeft, ChevronRight, ClipboardX, Flag, Timer, Trophy } from 'lucide-react';

function formatTime(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

// A reload loses React Router's in-memory location.state entirely, so
// without this a refresh would call fetchQueue() again from scratch - for
// a custom paper that means a brand-new random selection (the endpoint
// isn't idempotent), silently orphaning the per-question drafts ExamCanvas
// just saved. This mirrors the whole session shell (which questions, where
// you were, your score/timer) to localStorage so a reload resumes the
// exact same attempt instead of starting a different one.
const SESSION_STORAGE_KEY = 'acexam_practice_session_v1';

interface StoredSession {
  // React Router's location.key identifies this specific navigation entry -
  // stable across a reload of the same page, but a fresh value every time
  // navigate() pushes a new entry (e.g. starting a genuinely different
  // session). Comparing against it is what tells a reload of THIS session
  // apart from the user having navigated to a new one since.
  navKey: string;
  questions: Question[];
  currentIndex: number;
  sessionScore: { earned: number; possible: number };
  timeRemaining: number | null;
  attemptId: string | null;
}

function loadStoredSession(): StoredSession | null {
  try {
    const raw = localStorage.getItem(SESSION_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveStoredSession(s: StoredSession) {
  try { localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(s)); } catch { /* ignore */ }
}

function clearStoredSession() {
  try { localStorage.removeItem(SESSION_STORAGE_KEY); } catch { /* ignore */ }
}

// Groups sub-questions that belong to the same parent question number
// (e.g. "1(a)", "1(b)", "1(c)" or "01.1", "01.2") so they render together
// on one page, matching how they appear on a real exam paper.
function questionRootKey(qNum: string): string {
  const match = qNum.match(/^0*(\d+)/);
  return match ? match[1] : qNum;
}

function groupQuestions(questions: Question[]): Question[][] {
  const order: string[] = [];
  const map = new Map<string, Question[]>();
  questions.forEach(q => {
    // Scope grouping to the paper - two different papers can both have a
    // "question 4", but those are unrelated and must never be merged.
    const key = `${q.paper_id}::${questionRootKey(q.question_number || '')}`;
    if (!map.has(key)) {
      map.set(key, []);
      order.push(key);
    }
    map.get(key)!.push(q);
  });
  return order.map(key =>
    [...map.get(key)!].sort((a, b) =>
      (a.question_number || '').localeCompare(b.question_number || '', undefined, { numeric: true })
    )
  );
}

interface CustomSessionState {
  mode: 'custom';
  subject: string;
  examBoard: string;
  level: string;
  topicIds: string[];
}

interface AdaptiveSessionState {
  mode: 'adaptive';
}

interface ExamQuestionsSessionState {
  mode: 'exam_questions';
  subject: string;
  examBoard: string;
  level: string;
  topicMode: 'weak' | 'random' | 'select';
  topicIds: string[];
}

interface ReviewSessionState {
  mode: 'review';
  attemptId: string;
}

type SessionState = CustomSessionState | AdaptiveSessionState | ExamQuestionsSessionState | ReviewSessionState;

export default function PracticeSession() {
  const location = useLocation();
  const navigate = useNavigate();
  const sessionConfig: SessionState = (location.state as SessionState) || { mode: 'adaptive' };
  const isReview = sessionConfig.mode === 'review';
  const isEndless = sessionConfig.mode === 'exam_questions';

  const [questions, setQuestions] = useState<Question[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [focusReason, setFocusReason] = useState<any>(null);
  const [sessionCompleted, setSessionCompleted] = useState(false);
  const [sessionScore, setSessionScore] = useState({ earned: 0, possible: 0 });
  const [timeRemaining, setTimeRemaining] = useState<number | null>(null);
  const [attemptId, setAttemptId] = useState<string | null>(null);
  const fetchStartedRef = useRef(false);

  useEffect(() => {
    const stored = !isReview ? loadStoredSession() : null;
    if (stored && stored.navKey === location.key) {
      setQuestions(stored.questions);
      setCurrentIndex(stored.currentIndex);
      setSessionScore(stored.sessionScore);
      setTimeRemaining(stored.timeRemaining);
      setAttemptId(stored.attemptId);
      setFocusReason(null);
      setSessionCompleted(false);
      setLoading(false);
    } else {
      // Guard against React StrictMode's dev-only double-invocation of mount
      // effects: fetchQueue() creates a new `attempts` row server-side, so
      // firing it twice would split one session's answers across two rows.
      if (fetchStartedRef.current) return;
      fetchStartedRef.current = true;
      fetchQueue();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the stored session in sync as the student progresses, so a reload
  // at any point resumes exactly where they left off. Review sessions are
  // cheap and idempotent to re-fetch, so they skip local persistence.
  useEffect(() => {
    if (isReview || loading || sessionCompleted || questions.length === 0) return;
    saveStoredSession({ navKey: location.key, questions, currentIndex, sessionScore, timeRemaining, attemptId });
  }, [questions, currentIndex, sessionScore, timeRemaining, attemptId, loading, sessionCompleted, location.key, isReview]);

  useEffect(() => {
    if (sessionCompleted) clearStoredSession();
  }, [sessionCompleted]);

  // Custom papers carry a timer (real mock-exam feel); other modes never
  // set timeRemaining, so this is a no-op there. It's a guide, not an
  // enforcer - hitting 0 just switches the chip to "Time's up" rather than
  // forcing the session to end, since a student mid-answer shouldn't get
  // cut off.
  useEffect(() => {
    if (timeRemaining === null || sessionCompleted || timeRemaining <= 0) return;
    const id = setTimeout(() => setTimeRemaining(t => (t !== null ? t - 1 : t)), 1000);
    return () => clearTimeout(id);
  }, [timeRemaining, sessionCompleted]);

  const fetchQueue = async () => {
    setLoading(true);
    try {
      if (sessionConfig.mode === 'custom') {
        const res = await api.post('/generate/custom-paper', {
          subject: sessionConfig.subject,
          exam_board: sessionConfig.examBoard,
          level: sessionConfig.level,
          spec_topic_ids: sessionConfig.topicIds,
        });
        setQuestions(res.data.questions);
        setAttemptId(res.data.attempt_id);
        setFocusReason(null);
        setTimeRemaining(res.data.time_limit_seconds ?? null);
      } else if (sessionConfig.mode === 'exam_questions') {
        const res = await api.get('/generate/adaptive-queue', {
          params: {
            subject: sessionConfig.subject,
            exam_board: sessionConfig.examBoard,
            level: sessionConfig.level,
            topic_mode: sessionConfig.topicMode,
            topic_ids: sessionConfig.topicIds.join(',') || undefined,
          },
        });
        setQuestions(res.data.queue);
        setAttemptId(res.data.attempt_id);
        setFocusReason(res.data.focus_reason);
        setTimeRemaining(null);
      } else if (sessionConfig.mode === 'review') {
        const res = await api.get(`/generate/attempts/${sessionConfig.attemptId}`);
        setQuestions(res.data.questions);
        setAttemptId(sessionConfig.attemptId);
        setFocusReason(null);
        setTimeRemaining(null);
      } else {
        const res = await api.get('/generate/adaptive-queue');
        setQuestions(res.data.queue);
        setAttemptId(res.data.attempt_id);
        setFocusReason(res.data.focus_reason);
        setTimeRemaining(null);
      }
      setCurrentIndex(0);
      setSessionCompleted(false);
      setSessionScore({ earned: 0, possible: 0 });
    } catch (err) {
      console.error('Failed to load practice queue', err);
    } finally {
      setLoading(false);
    }
  };

  // Endless exam-questions mode: instead of ending the session when the
  // last group is reached, fetch another batch (excluding everything
  // already served this session) and append it, continuing until the
  // student explicitly quits.
  const loadMore = async (): Promise<boolean> => {
    if (sessionConfig.mode !== 'exam_questions' || !attemptId) return false;
    setLoadingMore(true);
    try {
      const res = await api.post(`/generate/adaptive-queue/${attemptId}/more`, null, {
        params: {
          topic_mode: sessionConfig.topicMode,
          topic_ids: sessionConfig.topicIds.join(',') || undefined,
          exclude_ids: questions.map(q => q.id).join(','),
        },
      });
      const more: Question[] = res.data.queue;
      if (more.length === 0) return false;
      setQuestions(prev => [...prev, ...more]);
      return true;
    } catch (err) {
      console.error('Failed to load more questions', err);
      return false;
    } finally {
      setLoadingMore(false);
    }
  };

  const handleAnswerSubmitted = (result: any) => {
    setSessionScore(prev => ({
      earned: prev.earned + result.marks_awarded,
      possible: prev.possible + result.marks_possible
    }));
  };

  const groups = useMemo(() => groupQuestions(questions), [questions]);

  // Navigation is free in both directions and never requires answering the
  // current question first - each group's ExamCanvas instance stays mounted
  // (just hidden) so moving away and back never loses unsubmitted typing or
  // ink. Finishing the session is a separate, explicit action.
  const goNext = () => setCurrentIndex(prev => Math.min(prev + 1, groups.length - 1));
  const goPrev = () => setCurrentIndex(prev => Math.max(prev - 1, 0));
  // Review mode never submits new answers, so sessionScore stays at its
  // initial {0, 0} the whole time - showing the "Session complete" screen
  // there would present a fake 0/0 "100% accuracy" result completely
  // disconnected from the real, already-recorded score of the paper being
  // reviewed. Just leave review the way the student got here instead.
  const finishSession = () => {
    if (isReview) {
      navigate(-1);
      return;
    }
    setSessionCompleted(true);
  };

  const advanceOrLoadMore = async () => {
    if (isEndless && currentIndex >= groups.length - 1) {
      const gotMore = await loadMore();
      if (gotMore) {
        setCurrentIndex(prev => prev + 1);
        return;
      }
    }
    goNext();
  };

  if (loading) {
    return (
      <Spinner
        label={
          sessionConfig.mode === 'adaptive' ? 'Finding the topics that need your attention most…' :
          sessionConfig.mode === 'review' ? 'Loading your previous answers…' :
          `Gathering questions for ${sessionConfig.subject}…`
        }
      />
    );
  }

  if (sessionCompleted) {
    const pct = sessionScore.possible > 0 ? Math.round((sessionScore.earned / sessionScore.possible) * 100) : null;
    return (
      <div className="container" style={{ maxWidth: '650px', marginTop: '3rem', textAlign: 'center' }}>
        <div className="card" style={{ padding: '3rem' }}>
          <Trophy size={40} strokeWidth={1.5} style={{ color: 'var(--accent-color)', marginBottom: '0.75rem' }} aria-hidden="true" />
          <h2 style={{ marginBottom: '0.5rem' }}>Session complete</h2>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
            Nice work — your mastery scores have been updated with what you just did.
          </p>

          <div style={{
            display: 'flex',
            justifyContent: 'center',
            gap: '2rem',
            background: 'var(--bg-tertiary)',
            border: '1px solid var(--border)',
            padding: '1.5rem',
            borderRadius: '12px',
            marginBottom: '2rem'
          }}>
            <div>
              <div style={{ fontSize: '2rem', fontWeight: 800, fontFamily: 'var(--font-heading)', color: 'var(--accent-color)' }}>
                {sessionScore.earned} / {sessionScore.possible}
              </div>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Marks earned</div>
            </div>
            <div>
              <div style={{ fontSize: '2rem', fontWeight: 800, fontFamily: 'var(--font-heading)', color: pct === null ? 'var(--text-secondary)' : (pct >= 70 ? 'var(--success)' : 'var(--warning)') }}>
                {pct === null ? '—' : `${pct}%`}
              </div>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Accuracy</div>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center', flexWrap: 'wrap' }}>
            {!isReview && (
              <button onClick={fetchQueue} className="btn btn-primary">
                Start another queue
              </button>
            )}
            <button onClick={() => navigate('/app')} className="btn btn-outline">
              Change practice setup
            </button>
            <a
              href={
                'subject' in sessionConfig
                  ? `/app/subject/${encodeURIComponent(subjectKey({ level: sessionConfig.level, exam_board: sessionConfig.examBoard, subject: sessionConfig.subject }))}/analytics`
                  : '/app'
              }
              className="btn btn-outline"
            >
              View analytics
            </a>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="container" style={{ marginTop: '2rem', marginBottom: '4rem' }}>
      {/* Session Progress Header */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        maxWidth: '850px',
        margin: '0 auto 1.5rem auto'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
          <span style={{
            background: 'var(--accent-color)',
            color: '#ffffff',
            padding: '0.2rem 0.6rem',
            borderRadius: '6px',
            fontSize: '0.85rem',
            fontWeight: 700
          }}>
            {isReview ? 'Reviewing' : `Question ${currentIndex + 1} of ${groups.length}`}
          </span>
          {focusReason && focusReason.active_misconceptions_count > 0 && (
            <span style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.3rem',
              background: 'var(--danger-bg)',
              color: 'var(--danger)',
              padding: '0.2rem 0.6rem',
              borderRadius: '6px',
              fontSize: '0.8rem'
            }}>
              <AlertTriangle size={13} aria-hidden="true" />
              Targeting {focusReason.active_misconceptions_count} active misconception(s)
            </span>
          )}
          {timeRemaining !== null && (
            <span style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.3rem',
              background: timeRemaining <= 120 ? 'var(--danger-bg)' : 'var(--bg-tertiary)',
              color: timeRemaining <= 120 ? 'var(--danger)' : 'var(--text-primary)',
              border: timeRemaining <= 120 ? 'none' : '1px solid var(--border)',
              padding: '0.2rem 0.6rem',
              borderRadius: '6px',
              fontSize: '0.85rem',
              fontWeight: 700,
              fontVariantNumeric: 'tabular-nums'
            }}>
              <Timer size={13} aria-hidden="true" />
              {timeRemaining > 0 ? formatTime(timeRemaining) : "Time's up"}
            </span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '1.25rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <button
              className="btn btn-outline"
              onClick={goPrev}
              disabled={currentIndex === 0}
              aria-label="Previous question"
              style={{ padding: '0.3rem 0.6rem' }}
            >
              <ChevronLeft size={16} aria-hidden="true" />
            </button>
            <button
              className="btn btn-outline"
              onClick={goNext}
              disabled={currentIndex >= groups.length - 1}
              aria-label="Next question"
              style={{ padding: '0.3rem 0.6rem' }}
            >
              <ChevronRight size={16} aria-hidden="true" />
            </button>
            <button
              className="btn btn-outline"
              onClick={finishSession}
              style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', padding: '0.3rem 0.7rem' }}
            >
              <Flag size={14} aria-hidden="true" />
              {isReview ? 'Done' : isEndless ? 'Quit' : 'Finish'}
            </button>
          </div>
          {!isReview && (
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
              Score: <strong style={{ color: 'var(--text-primary)' }}>{sessionScore.earned} / {sessionScore.possible}</strong>
            </div>
          )}
        </div>
      </div>

      {/* The Exam Paper Canvas. Every group's ExamCanvas stays mounted (just
          hidden) rather than swapping which group's props feed one shared
          instance, so navigating away and back never resets a group's
          unsubmitted typing/ink - each instance keeps its own local state
          for as long as the session lasts. */}
      {groups.length > 0 ? (
        groups.map((group, index) => {
          const isLast = index === groups.length - 1;
          const onNext = isLast
            ? (isEndless ? advanceOrLoadMore : finishSession)
            : (isEndless ? advanceOrLoadMore : goNext);
          const nextLabel = isLast
            ? (isEndless ? (loadingMore ? 'Loading…' : 'Next question') : (isReview ? 'Done' : 'Finish session'))
            : 'Next question';
          return (
            <div key={group.map(q => q.id).join('|')} style={{ display: index === currentIndex ? 'block' : 'none' }}>
              <ExamCanvas
                questions={group}
                attemptId={attemptId ?? undefined}
                reviewMode={isReview}
                onAnswerSubmitted={handleAnswerSubmitted}
                onNext={onNext}
                nextLabel={nextLabel}
              />
            </div>
          );
        })
      ) : (
        <div style={{ maxWidth: '600px', margin: '0 auto' }} className="card">
          <EmptyState
            icon={<ClipboardX size={40} strokeWidth={1.5} />}
            title="Nothing to practice here yet"
            description="There aren't any questions available for this selection right now."
            action={
              <button onClick={() => navigate('/app')} className="btn btn-outline">
                Change practice setup
              </button>
            }
          />
        </div>
      )}
    </div>
  );
}
