import React, { useEffect, useMemo, useState } from 'react';
import api from './api';
import { groupByTopicHierarchy } from './topicHierarchy';

function masteryColor(scorePct: number) {
  if (scorePct >= 75) return '#10b981';
  if (scorePct >= 45) return '#f59e0b';
  return '#ef4444';
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
    return (
      <div className="container text-center" style={{ marginTop: '4rem' }}>
        <h2>Loading your Master Student Profile...</h2>
      </div>
    );
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem' }}>
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: '2.2rem', marginBottom: '0.5rem' }}>Master Student Profile</h1>
        <p style={{ color: 'var(--text-secondary)' }}>
          Real-time multi-dimensional competence model powered by EWMA mastery, Ebbinghaus memory decay curves, and persistent misconception memory.
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
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Projected Trajectory</div>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#dc2626' }}>
            {data?.predicted_grade || 'Calibrating'}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>Based on 9-1 grade boundaries</div>
        </div>

        <div className="card">
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Overall Accuracy</div>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#10b981' }}>
            {data?.overall_accuracy_pct}%
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
            {data?.total_marks_earned} / {data?.total_marks_possible} Marks Earned
          </div>
        </div>

        <div className="card">
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Total Questions Marked</div>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#f59e0b' }}>
            {data?.total_answers || 0}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>Deterministic DSL & Spec Model</div>
        </div>

        <div className="card">
          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Active Misconceptions</div>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#ef4444' }}>
            {data?.misconceptions?.filter((m: any) => m.status === 'active').length || 0}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>Priority practice targets</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))', gap: '2rem' }}>
        {/* Specification Topic Mastery Tree Heatmap */}
        <div className="card">
          <h3 style={{ marginBottom: '1.25rem' }}>
            Specification Topic Mastery Heatmap
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
            {subjectGroups.length === 0 && (
              <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>No topic data yet.</p>
            )}
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
                    <span style={{ color: masteryColor(subjectPct), fontSize: '0.9rem' }}>
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
                            <span style={{ color: masteryColor(subPct), fontSize: '0.85rem' }}>{subPct}% avg</span>
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
                                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.4rem' }}>
                                    <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>
                                      {topic.spec_code} {topic.title}
                                    </span>
                                    <span style={{ fontWeight: 700, color: badgeColor, fontSize: '0.85rem' }}>
                                      {scorePct}% Mastery
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
        </div>

        {/* Command Word Competency Matrix & Misconception Memory */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
          {/* Command Word Matrix */}
          <div className="card">
            <h3 style={{ marginBottom: '1.25rem' }}>
              Command Word Competency Matrix
            </h3>
            {data?.command_word_competency?.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                {data.command_word_competency.map((cw: any, i: number) => (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontWeight: 600, color: '#cbd5e1' }}>{cw.command_word}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                      <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                        {cw.marks_awarded} / {cw.marks_possible}
                      </span>
                      <span style={{
                        fontWeight: 700,
                        padding: '0.2rem 0.5rem',
                        borderRadius: '4px',
                        fontSize: '0.8rem',
                        background: cw.accuracy_pct >= 70 ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)',
                        color: cw.accuracy_pct >= 70 ? '#34d399' : '#f87171'
                      }}>
                        {cw.accuracy_pct}%
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                Practice answers with command words (e.g. *Explain*, *Evaluate*, *Calculate*) to populate your matrix.
              </p>
            )}
          </div>

          {/* Misconception Memory List */}
          <div className="card">
            <h3 style={{ marginBottom: '1.25rem' }}>
              Persistent Misconception Memory
            </h3>
            {data?.misconceptions?.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                {data.misconceptions.map((m: any, i: number) => (
                  <div key={i} style={{
                    padding: '0.75rem',
                    borderRadius: '8px',
                    background: m.status === 'resolved' ? 'rgba(16, 185, 129, 0.08)' : 'rgba(239, 68, 68, 0.08)',
                    border: `1px solid ${m.status === 'resolved' ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)'}`
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem' }}>
                      <strong style={{ color: m.status === 'resolved' ? '#34d399' : '#f87171', fontSize: '0.9rem' }}>
                        {m.label || m.tag_id}
                      </strong>
                      <span style={{
                        fontSize: '0.75rem',
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        color: m.status === 'resolved' ? '#10b981' : '#f87171'
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
              <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                Zero active misconceptions recorded. The system will automatically tag and resolve recurring conceptual errors as you practice.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
