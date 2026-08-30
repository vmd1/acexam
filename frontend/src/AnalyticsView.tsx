import { useEffect, useMemo, useState } from 'react';
import api from './api';
import { groupByTopicHierarchy } from './topicHierarchy';
import Spinner from './Spinner';
import EmptyState from './EmptyState';
import { LineChart, CheckCircle2, AlertTriangle, XCircle, MessageSquareWarning } from 'lucide-react';

function masteryColor(scorePct: number) {
  if (scorePct >= 75) return 'var(--success)';
  if (scorePct >= 45) return 'var(--warning)';
  return 'var(--danger)';
}

function MasteryIcon({ scorePct }: { scorePct: number }) {
  if (scorePct >= 75) return <CheckCircle2 size={14} aria-hidden="true" />;
  if (scorePct >= 45) return <AlertTriangle size={14} aria-hidden="true" />;
  return <XCircle size={14} aria-hidden="true" />;
}

export default function AnalyticsView() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchProfile();
  }, []);

  const fetchProfile = async () => {
    try {
      const res = await api.get('/analytics/profile');
      setData(res.data);
    } catch (err) {
      console.error('Failed to load analytics', err);
    } finally {
      setLoading(false);
    }
  };

  const subjectGroups = useMemo(() => {
    const bySubject = new Map<string, any[]>();
    (data?.topic_mastery || []).forEach((t: any) => {
      const key = t.subject || 'Other';
      if (!bySubject.has(key)) bySubject.set(key, []);
      bySubject.get(key)!.push(t);
    });

    const avg = (arr: any[]) => (arr.length ? arr.reduce((s, t) => s + t.mastery_score, 0) / arr.length : 0);

    return Array.from(bySubject.entries()).map(([subject, topics]) => ({
      subject,
      avg: avg(topics),
      topicCount: topics.length,
      subTopics: groupByTopicHierarchy(topics).map(g => ({
        label: g.label,
        avg: avg(g.topics),
        topics: g.topics,
      })),
    }));
  }, [data]);

  if (loading) {
    return <Spinner label="Building your Master Student Profile…" />;
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem' }}>
      <div style={{ marginBottom: '2rem' }}>
        <h1>Master Student Profile</h1>
        <p style={{ color: 'var(--text-secondary)' }}>
          A real-time picture of where you stand: mastery per topic, memory decay over time, and misconceptions we're still watching.
        </p>
      </div>

      {/* Overview Stat Cards */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
        gap: '1.25rem',
        marginBottom: '2rem'
      }}>
        <div className="card">
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Projected grade</div>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: 'var(--accent-color)', fontFamily: 'var(--font-heading)' }}>
            {data?.predicted_grade || 'Calibrating'}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>Based on 9-1 grade boundaries</div>
        </div>

        <div className="card">
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Overall accuracy</div>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: 'var(--success)', fontFamily: 'var(--font-heading)' }}>
            {data?.overall_accuracy_pct}%
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
            {data?.total_marks_earned} / {data?.total_marks_possible} marks earned
          </div>
        </div>

        <div className="card">
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Questions marked</div>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: 'var(--warning)', fontFamily: 'var(--font-heading)' }}>
            {data?.total_answers || 0}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>Deterministic DSL & AI marking</div>
        </div>

        <div className="card">
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Active misconceptions</div>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: 'var(--danger)', fontFamily: 'var(--font-heading)' }}>
            {data?.misconceptions?.filter((m: any) => m.status === 'active').length || 0}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>Priority practice targets</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))', gap: '2rem' }}>
        {/* Specification Topic Mastery Tree Heatmap */}
        <div className="card">
          <h3>Topic mastery</h3>
          {subjectGroups.length === 0 ? (
            <EmptyState
              icon={<LineChart size={40} strokeWidth={1.5} />}
              title="Nothing to show yet"
              description="Answer a few practice questions and your topic-by-topic mastery will build up here."
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', marginTop: '1rem' }}>
              {subjectGroups.map(group => {
                const subjectPct = Math.round(group.avg * 100);
                return (
                  <details key={group.subject} style={{
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
                      <span>{group.subject}</span>
                      <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: masteryColor(subjectPct), fontSize: '0.9rem' }}>
                        <MasteryIcon scorePct={subjectPct} />
                        {subjectPct}% avg &middot; {group.topicCount} topics
                      </span>
                    </summary>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.75rem' }}>
                      {group.subTopics.map((sub: any) => {
                        const subPct = Math.round(sub.avg * 100);
                        return (
                          <details key={sub.label} style={{
                            background: 'var(--bg-tertiary)',
                            borderRadius: '6px',
                            border: '1px solid var(--border)',
                            padding: '0.5rem 0.75rem',
                            marginLeft: '0.5rem',
                          }}>
                            <summary style={{
                              cursor: 'pointer',
                              display: 'flex',
                              justifyContent: 'space-between',
                              alignItems: 'center',
                              fontWeight: 600,
                              fontSize: '0.9rem',
                            }}>
                              <span>{sub.label}</span>
                              <span style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', color: masteryColor(subPct), fontSize: '0.85rem' }}>
                                <MasteryIcon scorePct={subPct} />
                                {subPct}% avg
                              </span>
                            </summary>

                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', marginTop: '0.65rem' }}>
                              {sub.topics.map((topic: any) => {
                                const scorePct = Math.round(topic.mastery_score * 100);
                                const badgeColor = masteryColor(scorePct);
                                return (
                                  <div key={topic.id} style={{
                                    padding: '0.6rem 0.85rem',
                                    background: 'var(--bg-tertiary)',
                                    borderRadius: '8px',
                                    border: '1px solid var(--border)'
                                  }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.4rem', flexWrap: 'wrap', gap: '0.4rem' }}>
                                      <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>
                                        {topic.spec_code} {topic.title}
                                      </span>
                                      <span style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontWeight: 700, color: badgeColor, fontSize: '0.85rem' }}>
                                        <MasteryIcon scorePct={scorePct} />
                                        {scorePct}% mastery
                                      </span>
                                    </div>
                                    <div style={{
                                      width: '100%',
                                      height: '6px',
                                      background: 'var(--border)',
                                      borderRadius: '3px',
                                      overflow: 'hidden'
                                    }}>
                                      <div style={{
                                        width: `${scorePct}%`,
                                        height: '100%',
                                        background: badgeColor,
                                        transition: 'width 0.5s ease'
                                      }} />
                                    </div>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.4rem' }}>
                                      <span>Attempts: {topic.attempts_count}</span>
                                      <span>Decay score: {Math.round(topic.decay_score * 100)}%</span>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          </details>
                        );
                      })}
                    </div>
                  </details>
                );
              })}
            </div>
          )}
        </div>

        {/* Command Word Competency Matrix & Misconception Memory */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
          {/* Command Word Matrix */}
          <div className="card">
            <h3>Command word accuracy</h3>
            {data?.command_word_competency?.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '1rem' }}>
                {data.command_word_competency.map((cw: any, i: number) => (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{cw.command_word}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                      <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                        {cw.marks_awarded} / {cw.marks_possible}
                      </span>
                      <span style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.25rem',
                        fontWeight: 700,
                        padding: '0.2rem 0.5rem',
                        borderRadius: '4px',
                        fontSize: '0.8rem',
                        background: cw.accuracy_pct >= 70 ? 'var(--success-bg)' : 'var(--danger-bg)',
                        color: cw.accuracy_pct >= 70 ? 'var(--success)' : 'var(--danger)'
                      }}>
                        {cw.accuracy_pct >= 70 ? <CheckCircle2 size={12} aria-hidden="true" /> : <XCircle size={12} aria-hidden="true" />}
                        {cw.accuracy_pct}%
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState
                icon={<MessageSquareWarning size={36} strokeWidth={1.5} />}
                title="No command-word data yet"
                description="Answer questions that use command words like Explain, Evaluate or Calculate to start building this matrix."
              />
            )}
          </div>

          {/* Misconception Memory List */}
          <div className="card">
            <h3>Misconceptions we're watching</h3>
            {data?.misconceptions?.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '1rem' }}>
                {data.misconceptions.map((m: any, i: number) => (
                  <div key={i} style={{
                    padding: '0.75rem',
                    borderRadius: '8px',
                    background: m.status === 'resolved' ? 'var(--success-bg)' : 'var(--danger-bg)',
                    border: `1px solid ${m.status === 'resolved' ? 'var(--success)' : 'var(--danger)'}`,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem', flexWrap: 'wrap', gap: '0.4rem' }}>
                      <strong style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: m.status === 'resolved' ? 'var(--success)' : 'var(--danger)', fontSize: '0.9rem' }}>
                        {m.status === 'resolved' ? <CheckCircle2 size={14} aria-hidden="true" /> : <AlertTriangle size={14} aria-hidden="true" />}
                        {m.label || m.tag_id}
                      </strong>
                      <span style={{
                        fontSize: '0.75rem',
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        color: m.status === 'resolved' ? 'var(--success)' : 'var(--danger)'
                      }}>
                        {m.status === 'resolved' ? 'Resolved' : `Active (${m.occurrences}x)`}
                      </span>
                    </div>
                    <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                      {m.description || `Spec ${m.spec_code}`}
                    </div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
                      Consecutive correct answers towards resolution: {m.consecutive_correct}/3
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState
                icon={<CheckCircle2 size={36} strokeWidth={1.5} />}
                title="No misconceptions flagged"
                description="We'll tag and track recurring conceptual errors here automatically as you practice."
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
