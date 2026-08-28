import React, { useEffect, useState } from 'react';
import api from './api';

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
        <p style={{ color: '#94a3b8' }}>
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
          <div style={{ fontSize: '0.85rem', color: '#94a3b8', marginBottom: '0.5rem' }}>Projected Trajectory</div>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#6366f1' }}>
            {data?.predicted_grade || 'Calibrating'}
          </div>
          <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>Based on 9-1 grade boundaries</div>
        </div>

        <div className="card">
          <div style={{ fontSize: '0.85rem', color: '#94a3b8', marginBottom: '0.5rem' }}>Overall Accuracy</div>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#10b981' }}>
            {data?.overall_accuracy_pct}%
          </div>
          <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>
            {data?.total_marks_earned} / {data?.total_marks_possible} Marks Earned
          </div>
        </div>

        <div className="card">
          <div style={{ fontSize: '0.85rem', color: '#94a3b8', marginBottom: '0.5rem' }}>Total Questions Marked</div>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#f59e0b' }}>
            {data?.total_answers || 0}
          </div>
          <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>Deterministic DSL & Spec Model</div>
        </div>

        <div className="card">
          <div style={{ fontSize: '0.85rem', color: '#94a3b8', marginBottom: '0.5rem' }}>Active Misconceptions</div>
          <div style={{ fontSize: '2.2rem', fontWeight: 800, color: '#ef4444' }}>
            {data?.misconceptions?.filter((m: any) => m.status === 'active').length || 0}
          </div>
          <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>Priority practice targets</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))', gap: '2rem' }}>
        {/* Specification Topic Mastery Tree Heatmap */}
        <div className="card">
          <h3 style={{ marginBottom: '1.25rem' }}>
            Specification Topic Mastery Heatmap
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {data?.topic_mastery?.map((topic: any) => {
              const scorePct = Math.round(topic.mastery_score * 100);
              let badgeColor = '#ef4444';
              if (scorePct >= 75) badgeColor = '#10b981';
              else if (scorePct >= 45) badgeColor = '#f59e0b';

              return (
                <div key={topic.id} style={{
                  padding: '0.75rem 1rem',
                  background: 'rgba(255, 255, 255, 0.03)',
                  borderRadius: '8px',
                  border: '1px solid rgba(255, 255, 255, 0.05)'
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.4rem' }}>
                    <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>
                      {topic.spec_code} {topic.title}
                    </span>
                    <span style={{ fontWeight: 700, color: badgeColor, fontSize: '0.9rem' }}>
                      {scorePct}% Mastery
                    </span>
                  </div>
                  {/* Progress bar */}
                  <div style={{
                    width: '100%',
                    height: '6px',
                    background: 'rgba(255, 255, 255, 0.1)',
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
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: '#64748b', marginTop: '0.4rem' }}>
                    <span>Attempts: {topic.attempts_count}</span>
                    <span>Decay score: {Math.round(topic.decay_score * 100)}%</span>
                  </div>
                </div>
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
                      <span style={{ fontSize: '0.85rem', color: '#94a3b8' }}>
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
              <p style={{ color: '#64748b', fontSize: '0.9rem' }}>
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
                    <div style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
                      {m.description || `Spec ${m.spec_code}`}
                    </div>
                    <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.35rem' }}>
                      Consecutive correct answers towards resolution: {m.consecutive_correct}/3
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p style={{ color: '#64748b', fontSize: '0.9rem' }}>
                Zero active misconceptions recorded. The system will automatically tag and resolve recurring conceptual errors as you practice.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
