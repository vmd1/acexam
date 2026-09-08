import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from './api';
import Spinner from './Spinner';
import TopicPicker from './TopicPicker';
import { parseSubjectKey } from './PracticeSetup';

// Past-papers setup for one fixed subject (reached from SubjectPractice) -
// pick topics (or leave it random) and generate a saved mock paper.
export default function CustomPapers() {
  const { subjectKey = '' } = useParams();
  const navigate = useNavigate();
  const { level, exam_board: examBoard, subject } = parseSubjectKey(decodeURIComponent(subjectKey));

  const [tier, setTier] = useState<string | null>(null);
  const [selectedTopicIds, setSelectedTopicIds] = useState<Set<string>>(new Set());
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

  const startCustom = (random: boolean) => {
    navigate('/app/session', {
      state: {
        mode: 'custom',
        subject, examBoard, level,
        topicIds: random ? [] : Array.from(selectedTopicIds),
      },
    });
  };

  if (loading) {
    return <Spinner label="Pulling up your subject…" />;
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem', maxWidth: '900px' }}>
      <h1 style={{ marginBottom: '0.25rem' }}>Custom papers</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
        Build a mock {subject} paper from the question bank. Timed like a real paper, and never repeats a
        question you've already attempted. Saved automatically so you can come back to it later.
      </p>

      <div className="card">
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '1rem',
          flexWrap: 'wrap',
          gap: '0.75rem',
        }}>
          <h3 style={{ margin: 0 }}>Topics</h3>
          <div style={{ display: 'flex', gap: '0.75rem' }}>
            <button className="btn btn-outline" onClick={() => startCustom(true)}>Random topics</button>
            <button
              className="btn btn-primary"
              disabled={selectedTopicIds.size === 0}
              onClick={() => startCustom(false)}
            >
              Start selected ({selectedTopicIds.size})
            </button>
          </div>
        </div>
        <TopicPicker
          level={level}
          examBoard={examBoard}
          subject={subject}
          tier={tier}
          selectedTopicIds={selectedTopicIds}
          onChange={setSelectedTopicIds}
        />
      </div>
    </div>
  );
}
