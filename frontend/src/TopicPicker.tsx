import { useCallback, useEffect, useMemo, useState } from 'react';
import api, { extractErrorMessage } from './api';
import { groupByTopicHierarchy } from './topicHierarchy';
import Spinner from './Spinner';
import { AlertTriangle } from 'lucide-react';

interface Topic {
  id: string;
  exam_board: string;
  subject: string;
  level: string;
  spec_code: string;
  title: string;
  parent_id: string | null;
  tier_only: string | null;
}

interface MasteryInfo {
  mastery_score: number;
  attempts_count: number;
}

const WEAK_THRESHOLD = 0.45;

interface TopicPickerProps {
  level: string;
  examBoard: string;
  subject: string;
  tier?: string | null;
  selectedTopicIds: Set<string>;
  onChange: (ids: Set<string>) => void;
}

// Topic selection tree for one fixed subject/board/level - extracted out of
// CustomPapers.tsx so both past-paper generation and the "select topics"
// exam-questions mode share the same weak-topic-flagged picker UI.
export default function TopicPicker({ level, examBoard, subject, tier, selectedTopicIds, onChange }: TopicPickerProps) {
  const [topics, setTopics] = useState<Topic[]>([]);
  const [mastery, setMastery] = useState<Record<string, MasteryInfo>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchTopics = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [topicsRes, profileRes] = await Promise.all([
        api.get('/exams/topics'),
        api.get('/analytics/profile'),
      ]);
      const filtered: Topic[] = (topicsRes.data as Topic[]).filter(
        t => t.level === level && t.exam_board === examBoard && t.subject === subject &&
          (!t.tier_only || t.tier_only === tier)
      );
      setTopics(filtered);
      const m: Record<string, MasteryInfo> = {};
      (profileRes.data.topic_mastery || []).forEach((t: any) => {
        m[t.id] = { mastery_score: t.mastery_score, attempts_count: t.attempts_count };
      });
      setMastery(m);
    } catch (err) {
      console.error('Failed to load topics', err);
      setError(extractErrorMessage(err, 'Failed to load topics'));
      setTopics([]);
    } finally {
      setLoading(false);
    }
  }, [level, examBoard, subject, tier]);

  useEffect(() => {
    fetchTopics();
  }, [fetchTopics]);

  const isWeak = (topicId: string) => {
    const m = mastery[topicId];
    return !!m && m.attempts_count > 0 && m.mastery_score < WEAK_THRESHOLD;
  };

  const subTopicGroups = useMemo(() => groupByTopicHierarchy(topics), [topics]);

  const toggleTopic = (id: string) => {
    const next = new Set(selectedTopicIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(next);
  };

  const toggleSubTopic = (group: Topic[]) => {
    const next = new Set(selectedTopicIds);
    const allSelected = group.every(t => next.has(t.id));
    group.forEach(t => (allSelected ? next.delete(t.id) : next.add(t.id)));
    onChange(next);
  };

  if (loading) {
    return <Spinner label="Loading topics…" />;
  }

  if (error) {
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '0.75rem',
        padding: '0.85rem 1rem',
        background: 'var(--bg-tertiary)',
        border: '1px solid var(--danger)',
        borderRadius: '8px',
        color: 'var(--danger)',
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.9rem' }}>
          <AlertTriangle size={16} aria-hidden="true" />
          {error}
        </span>
        <button onClick={() => fetchTopics()} className="btn btn-secondary" style={{ flexShrink: 0 }}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      {subTopicGroups.map(({ label, topics: groupTopics }) => {
        const weakCount = groupTopics.filter(t => isWeak(t.id)).length;
        const selectedCount = groupTopics.filter(t => selectedTopicIds.has(t.id)).length;
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
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }} onClick={e => e.stopPropagation()}>
                <input
                  type="checkbox"
                  checked={selectedCount === groupTopics.length}
                  ref={el => { if (el) el.indeterminate = selectedCount > 0 && selectedCount < groupTopics.length; }}
                  onChange={() => toggleSubTopic(groupTopics)}
                  aria-label={`Select all topics in ${label}`}
                />
                <span>{label}</span>
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                {weakCount > 0 && (
                  <span style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', color: 'var(--danger)', fontSize: '0.8rem', fontWeight: 600 }}>
                    <AlertTriangle size={13} aria-hidden="true" />
                    {weakCount} need work
                  </span>
                )}
                <span style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>{selectedCount}/{groupTopics.length} selected</span>
              </span>
            </summary>

            <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '0.6rem 0' }}>
              <button
                onClick={(e) => { e.preventDefault(); toggleSubTopic(groupTopics); }}
                className="btn btn-outline"
                style={{ padding: '0.15rem 0.5rem', fontSize: '0.75rem' }}
              >
                {groupTopics.every(t => selectedTopicIds.has(t.id)) ? 'Deselect all' : 'Select all'}
              </button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {groupTopics.map(t => {
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
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleTopic(t.id)}
                        aria-label={`${t.spec_code} ${t.title}`}
                      />
                      <span>{t.spec_code} {t.title}</span>
                    </span>
                    {weak ? (
                      <span style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.75rem', color: 'var(--danger)', fontWeight: 700 }}>
                        <AlertTriangle size={13} aria-hidden="true" />
                        Needs work
                      </span>
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
  );
}
