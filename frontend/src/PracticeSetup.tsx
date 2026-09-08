import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from './api';
import Spinner from './Spinner';
import EmptyState from './EmptyState';
import { iconForSubject } from './subjectIcons';
import { BookOpen } from 'lucide-react';

interface UserSubject {
  id: string;
  exam_board: string;
  level: string;
  subject: string;
  tier?: string | null;
}

export function subjectKey(s: { level: string; exam_board: string; subject: string }) {
  return `${s.level}::${s.exam_board}::${s.subject}`;
}

export function parseSubjectKey(key: string) {
  const [level, exam_board, subject] = key.split('::');
  return { level, exam_board, subject };
}

// Practice hub: one card per subject the student has added, click through to
// SubjectPractice for "Exam questions" vs "Custom papers".
export default function PracticeSetup() {
  const navigate = useNavigate();
  const [subjects, setSubjects] = useState<UserSubject[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const res = await api.get('/auth/me/subjects');
        setSubjects(res.data);
      } catch (err) {
        console.error('Failed to load subjects', err);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) {
    return <Spinner label="Loading your subjects…" />;
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem', maxWidth: '900px' }}>
      <h1 style={{ marginBottom: '0.5rem' }}>Practice</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
        Pick a subject to start practicing - endless exam questions, or a full timed mock paper.
      </p>

      {subjects.length === 0 ? (
        <EmptyState
          icon={<BookOpen size={40} strokeWidth={1.5} />}
          title="No subjects yet"
          description="Add a subject from your account settings to start practicing."
        />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '1rem' }}>
          {subjects.map(s => {
            const Icon = iconForSubject(s.subject);
            return (
              <button
                key={s.id}
                className="card"
                style={{ textAlign: 'left', cursor: 'pointer' }}
                onClick={() => navigate(`/app/subject/${encodeURIComponent(subjectKey(s))}`)}
              >
                <span className="hero-stat-icon" aria-hidden="true" style={{ marginBottom: '0.75rem' }}>
                  <Icon size={20} />
                </span>
                <div style={{ fontWeight: 700 }}>{s.subject}</div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                  {s.level} {s.exam_board}{s.tier ? ` · ${s.tier}` : ''}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
