import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from './api';
import Spinner from './Spinner';
import TopicPicker from './TopicPicker';
import { parseSubjectKey } from './PracticeSetup';
import { Shuffle, AlertTriangle, ListChecks } from 'lucide-react';

type TopicMode = 'weak' | 'random' | 'select';

// Setup screen for the endless "exam questions" practice mode: random
// topics, auto-targeted weak topics (default), or a manual topic selection.
export default function ExamQuestionsSetup() {
  const { subjectKey = '' } = useParams();
  const navigate = useNavigate();
  const { level, exam_board: examBoard, subject } = parseSubjectKey(decodeURIComponent(subjectKey));

  const [topicMode, setTopicMode] = useState<TopicMode>('weak');
  const [selectedTopicIds, setSelectedTopicIds] = useState<Set<string>>(new Set());
  const [tier, setTier] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const res = await api.get('/auth/me/subjects');
        const match = (res.data as any[]).find(
          s => s.level === level && s.exam_board === examBoard && s.subject === subject
        );
        setTier(match?.tier ?? null);
      } catch (err) {
        console.error('Failed to load subject tier', err);
      } finally {
        setLoading(false);
      }
    })();
  }, [level, examBoard, subject]);

  const start = () => {
    navigate('/app/session', {
      state: {
        mode: 'exam_questions',
        subject, examBoard, level,
        topicMode,
        topicIds: topicMode === 'select' ? Array.from(selectedTopicIds) : [],
      },
    });
  };

  if (loading) {
    return <Spinner label="Loading…" />;
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem', maxWidth: '900px' }}>
      <h1 style={{ marginBottom: '0.25rem' }}>Exam questions</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
        {subject} - endless practice questions. Keep going until you quit.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem', marginBottom: '2rem' }}>
        <button
          className="card"
          style={{ textAlign: 'left', cursor: 'pointer', border: topicMode === 'weak' ? '2px solid var(--accent-color)' : '1px solid var(--border)' }}
          onClick={() => setTopicMode('weak')}
        >
          <span className="hero-stat-icon" aria-hidden="true" style={{ marginBottom: '0.5rem' }}><AlertTriangle size={18} /></span>
          <div style={{ fontWeight: 700 }}>Weak topics</div>
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Auto-targets what you're struggling with.</div>
        </button>
        <button
          className="card"
          style={{ textAlign: 'left', cursor: 'pointer', border: topicMode === 'random' ? '2px solid var(--accent-color)' : '1px solid var(--border)' }}
          onClick={() => setTopicMode('random')}
        >
          <span className="hero-stat-icon" aria-hidden="true" style={{ marginBottom: '0.5rem' }}><Shuffle size={18} /></span>
          <div style={{ fontWeight: 700 }}>Random</div>
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Any topic, mixed up.</div>
        </button>
        <button
          className="card"
          style={{ textAlign: 'left', cursor: 'pointer', border: topicMode === 'select' ? '2px solid var(--accent-color)' : '1px solid var(--border)' }}
          onClick={() => setTopicMode('select')}
        >
          <span className="hero-stat-icon" aria-hidden="true" style={{ marginBottom: '0.5rem' }}><ListChecks size={18} /></span>
          <div style={{ fontWeight: 700 }}>Select topics</div>
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>Choose exactly what to practice.</div>
        </button>
      </div>

      {topicMode === 'select' && (
        <div className="card" style={{ marginBottom: '2rem' }}>
          <TopicPicker
            level={level}
            examBoard={examBoard}
            subject={subject}
            tier={tier}
            selectedTopicIds={selectedTopicIds}
            onChange={setSelectedTopicIds}
          />
        </div>
      )}

      <button
        className="btn btn-primary"
        onClick={start}
        disabled={topicMode === 'select' && selectedTopicIds.size === 0}
      >
        Start
      </button>
    </div>
  );
}
