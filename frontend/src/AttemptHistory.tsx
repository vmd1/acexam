import { useEffect, useState } from 'react';
import api from './api';
import Spinner from './Spinner';
import EmptyState from './EmptyState';
import { AttemptRow, type AttemptSummary } from './SubjectPractice';
import { History as HistoryIcon } from 'lucide-react';

type Tab = 'custom' | 'exam_questions';

// Global cross-subject history: saved past papers and exam-question
// sessions, each opening into review mode (see PracticeSession.tsx).
export default function AttemptHistory() {
  const [tab, setTab] = useState<Tab>('custom');
  const [attempts, setAttempts] = useState<AttemptSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    (async () => {
      try {
        const res = await api.get('/generate/attempts', { params: { mode: tab } });
        setAttempts(res.data);
      } catch (err) {
        console.error('Failed to load history', err);
      } finally {
        setLoading(false);
      }
    })();
  }, [tab]);

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem', maxWidth: '900px' }}>
      <h1 style={{ marginBottom: '0.5rem' }}>History</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
        Revisit any saved paper or past session - review your answers and re-attempt any question.
      </p>

      <div className="level-tabs" role="tablist" aria-label="History type" style={{ marginBottom: '1.5rem' }}>
        <button
          role="tab"
          aria-selected={tab === 'custom'}
          className={`level-tab${tab === 'custom' ? ' active' : ''}`}
          onClick={() => setTab('custom')}
        >
          Custom papers
        </button>
        <button
          role="tab"
          aria-selected={tab === 'exam_questions'}
          className={`level-tab${tab === 'exam_questions' ? ' active' : ''}`}
          onClick={() => setTab('exam_questions')}
        >
          Exam sessions
        </button>
      </div>

      {loading ? (
        <Spinner label="Loading history…" />
      ) : attempts.length === 0 ? (
        <EmptyState
          icon={<HistoryIcon size={40} strokeWidth={1.5} />}
          title="Nothing here yet"
          description={tab === 'custom' ? "Generate a past paper from any subject to see it here." : "Practice some exam questions to see your sessions here."}
        />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {attempts.map(a => <AttemptRow key={a.id} attempt={a} />)}
        </div>
      )}
    </div>
  );
}
