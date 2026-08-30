import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from './api';
import { groupByTopicHierarchy } from './topicHierarchy';

interface Topic {
  id: string;
  exam_board: string;
  subject: string;
  level: string;
  spec_code: string;
  title: string;
  parent_id: string | null;
}

interface MasteryInfo {
  mastery_score: number;
  attempts_count: number;
}

const WEAK_THRESHOLD = 0.45;

export default function PracticeSetup() {
  const navigate = useNavigate();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [mastery, setMastery] = useState<Record<string, MasteryInfo>>({});
  const [loading, setLoading] = useState(true);
  const [selectedSubjectKey, setSelectedSubjectKey] = useState<string | null>(null);
  const [selectedTopicIds, setSelectedTopicIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    (async () => {
      try {
        const [topicsRes, profileRes] = await Promise.all([
          api.get('/exams/topics'),
          api.get('/analytics/profile'),
        ]);
        setTopics(topicsRes.data);
        const m: Record<string, MasteryInfo> = {};
        (profileRes.data.topic_mastery || []).forEach((t: any) => {
          m[t.id] = { mastery_score: t.mastery_score, attempts_count: t.attempts_count };
        });
        setMastery(m);
      } catch (err) {
        console.error('Failed to load practice setup data', err);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const subjectGroups = useMemo(() => {
    const map = new Map<string, { exam_board: string; subject: string; level: string; topics: Topic[] }>();
    topics.forEach(t => {
      const key = `${t.level}::${t.exam_board}::${t.subject}`;
      if (!map.has(key)) map.set(key, { exam_board: t.exam_board, subject: t.subject, level: t.level, topics: [] });
      map.get(key)!.topics.push(t);
    });
    return Array.from(map.entries()).map(([key, v]) => ({ key, ...v }));
  }, [topics]);

  const isWeak = (topicId: string) => {
    const m = mastery[topicId];
    return !!m && m.attempts_count > 0 && m.mastery_score < WEAK_THRESHOLD;
  };

  const currentGroup = subjectGroups.find(g => g.key === selectedSubjectKey);

  const subTopicGroups = useMemo(() => {
    if (!currentGroup) return [];
    return groupByTopicHierarchy(currentGroup.topics);
  }, [currentGroup]);

  const toggleTopic = (id: string) => {
    setSelectedTopicIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSubTopic = (topics: Topic[]) => {
    setSelectedTopicIds(prev => {
      const next = new Set(prev);
      const allSelected = topics.every(t => next.has(t.id));
      topics.forEach(t => (allSelected ? next.delete(t.id) : next.add(t.id)));
      return next;
    });
  };

  const startAdaptive = () => {
    navigate('/app/session', { state: { mode: 'adaptive' } });
  };

  const startCustom = (random: boolean) => {
    if (!currentGroup) return;
    navigate('/app/session', {
      state: {
        mode: 'custom',
        subject: currentGroup.subject,
        examBoard: currentGroup.exam_board,
        topicIds: random ? [] : Array.from(selectedTopicIds),
      },
    });
  };

  if (loading) {
    return <div className="container text-center" style={{ marginTop: '4rem' }}>Loading subjects...</div>;
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem', maxWidth: '900px' }}>
      <h1 style={{ marginBottom: '0.5rem' }}>Start Practicing</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
        Choose a subject and specific topics, jump into random questions, or let the adaptive engine target your weakest areas.
      </p>

      <div className="card" style={{
        marginBottom: '2rem',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: '1rem',
      }}>
        <div>
          <h3 style={{ marginBottom: '0.25rem' }}>Adaptive Practice</h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', margin: 0 }}>
            Automatically targets your weakest topics, active misconceptions, and decaying memory.
          </p>
        </div>
        <button className="btn btn-primary" onClick={startAdaptive}>Start Adaptive Session</button>
      </div>

      <h3 style={{ marginBottom: '1rem' }}>Or choose a subject</h3>
      {subjectGroups.length === 0 ? (
        <p style={{ color: 'var(--text-muted)' }}>No subjects available yet.</p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '1rem', marginBottom: '2rem' }}>
          {subjectGroups.map(g => {
            const weakCount = g.topics.filter(t => isWeak(t.id)).length;
            const selected = selectedSubjectKey === g.key;
            return (
              <button
                key={g.key}
                onClick={() => { setSelectedSubjectKey(g.key); setSelectedTopicIds(new Set()); }}
                className="card"
                style={{
                  textAlign: 'left',
                  cursor: 'pointer',
                  border: selected ? '2px solid #dc2626' : '1px solid var(--border)',
                }}
              >
                <div style={{ fontWeight: 700 }}>{g.subject}</div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{g.level} {g.exam_board} &middot; {g.topics.length} topics</div>
                {weakCount > 0 && (
                  <div style={{ fontSize: '0.75rem', color: '#f87171', marginTop: '0.4rem', fontWeight: 600 }}>
                    {weakCount} topic{weakCount > 1 ? 's' : ''} need work
                  </div>
                )}
              </button>
            );
          })}
        </div>
      )}

      {currentGroup && (
        <div className="card">
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: '1rem',
            flexWrap: 'wrap',
            gap: '0.75rem',
          }}>
            <h3 style={{ margin: 0 }}>{currentGroup.subject} topics</h3>
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
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {subTopicGroups.map(({ label, topics }) => {
              const weakCount = topics.filter(t => isWeak(t.id)).length;
              const selectedCount = topics.filter(t => selectedTopicIds.has(t.id)).length;
              return (
                <details key={label} style={{
                  background: 'var(--bg-tertiary)',
                  borderRadius: '8px',
                  border: '1px solid var(--border)',
                  padding: '0.6rem 0.9rem',
                }}>
                  <summary style={{
                    cursor: 'pointer',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    fontWeight: 700,
                  }}>
                    <span>{label}</span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                      {weakCount > 0 && (
                        <span style={{ color: '#f87171', fontSize: '0.8rem', fontWeight: 600 }}>
                          {weakCount} need work
                        </span>
                      )}
                      <span style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>{selectedCount}/{topics.length} selected</span>
                    </span>
                  </summary>

                  <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '0.6rem 0' }}>
                    <button
                      onClick={(e) => { e.preventDefault(); toggleSubTopic(topics); }}
                      className="btn btn-outline"
                      style={{ padding: '0.15rem 0.5rem', fontSize: '0.75rem' }}
                    >
                      {topics.every(t => selectedTopicIds.has(t.id)) ? 'Deselect all' : 'Select all'}
                    </button>
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                    {topics.map(t => {
                      const weak = isWeak(t.id);
                      const m = mastery[t.id];
                      const checked = selectedTopicIds.has(t.id);
                      return (
                        <label key={t.id} style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          padding: '0.6rem 0.9rem',
                          borderRadius: '8px',
                          background: 'var(--bg-tertiary)',
                          border: weak ? '1px solid rgba(239, 68, 68, 0.4)' : '1px solid var(--border)',
                          cursor: 'pointer',
                        }}>
                          <span style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                            <input type="checkbox" checked={checked} onChange={() => toggleTopic(t.id)} />
                            <span>{t.spec_code} {t.title}</span>
                          </span>
                          {weak ? (
                            <span style={{ fontSize: '0.75rem', color: '#f87171', fontWeight: 700 }}>Needs work</span>
                          ) : m && m.attempts_count > 0 ? (
                            <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{Math.round(m.mastery_score * 100)}% mastery</span>
                          ) : (
                            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Not attempted</span>
                          )}
                        </label>
                      );
                    })}
                  </div>
                </details>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
