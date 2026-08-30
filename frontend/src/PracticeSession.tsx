import { useState, useEffect, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import api from './api';
import ExamCanvas from './ExamCanvas';
import type { Question } from './types';
import Spinner from './Spinner';
import EmptyState from './EmptyState';
import { AlertTriangle, ChevronLeft, ChevronRight, ClipboardX, Flag, Timer, Trophy } from 'lucide-react';

function formatTime(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
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

type SessionState = CustomSessionState | AdaptiveSessionState;

export default function PracticeSession() {
  const location = useLocation();
  const navigate = useNavigate();
  const sessionConfig: SessionState = (location.state as SessionState) || { mode: 'adaptive' };

  const [questions, setQuestions] = useState<Question[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [focusReason, setFocusReason] = useState<any>(null);
  const [sessionCompleted, setSessionCompleted] = useState(false);
  const [sessionScore, setSessionScore] = useState({ earned: 0, possible: 0 });
  const [timeRemaining, setTimeRemaining] = useState<number | null>(null);

  useEffect(() => {
    fetchQueue();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Custom papers carry a timer (real mock-exam feel); the adaptive queue
  // never sets timeRemaining, so this is a no-op there. It's a guide, not
  // an enforcer - hitting 0 just switches the chip to "Time's up" rather
  // than forcing the session to end, since a student mid-answer shouldn't
  // get cut off.
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
        setFocusReason(null);
        setTimeRemaining(res.data.time_limit_seconds ?? null);
      } else {
        const res = await api.get('/generate/adaptive-queue');
        setQuestions(res.data.queue);
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
  const finishSession = () => setSessionCompleted(true);

  if (loading) {
    return (
      <Spinner
        label={sessionConfig.mode === 'adaptive'
          ? 'Finding the topics that need your attention most…'
          : `Gathering questions for ${sessionConfig.subject}…`}
      />
    );
  }

  if (sessionCompleted) {
    const pct = sessionScore.possible > 0 ? Math.round((sessionScore.earned / sessionScore.possible) * 100) : 100;
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
              <div style={{ fontSize: '2rem', fontWeight: 800, fontFamily: 'var(--font-heading)', color: pct >= 70 ? 'var(--success)' : 'var(--warning)' }}>
                {pct}%
              </div>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Accuracy</div>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center', flexWrap: 'wrap' }}>
            <button onClick={fetchQueue} className="btn btn-primary">
              Start another queue
            </button>
            <button onClick={() => navigate('/app')} className="btn btn-outline">
              Change practice setup
            </button>
            <a href="/analytics" className="btn btn-outline">
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
            Question {currentIndex + 1} of {groups.length}
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
              Finish
            </button>
          </div>
          <div style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
            Score: <strong style={{ color: 'var(--text-primary)' }}>{sessionScore.earned} / {sessionScore.possible}</strong>
          </div>
        </div>
      </div>

      {/* The Exam Paper Canvas. Every group's ExamCanvas stays mounted (just
          hidden) rather than swapping which group's props feed one shared
          instance, so navigating away and back never resets a group's
          unsubmitted typing/ink - each instance keeps its own local state
          for as long as the session lasts. */}
      {groups.length > 0 ? (
        groups.map((group, index) => (
          <div key={group.map(q => q.id).join('|')} style={{ display: index === currentIndex ? 'block' : 'none' }}>
            <ExamCanvas
              questions={group}
              onAnswerSubmitted={handleAnswerSubmitted}
              onNext={index < groups.length - 1 ? goNext : finishSession}
              nextLabel={index < groups.length - 1 ? 'Next question' : 'Finish session'}
            />
          </div>
        ))
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
