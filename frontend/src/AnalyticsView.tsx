import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import api from './api';
import { groupByTopicHierarchy } from './topicHierarchy';
import { parseSubjectKey } from './PracticeSetup';
import Spinner from './Spinner';
import EmptyState from './EmptyState';
import { LineChart, CheckCircle2, AlertTriangle, XCircle, MessageSquareWarning, ArrowLeft } from 'lucide-react';

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
  const { subjectKey = '' } = useParams();
  const { level, exam_board, subject } = parseSubjectKey(decodeURIComponent(subjectKey));

  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchProfile();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subject, exam_board, level]);

  const fetchProfile = async () => {
    setLoading(true);
    try {
      const res = await api.get('/analytics/profile', { params: { subject, exam_board, level } });
      setData(res.data);
    } catch (err) {
      console.error('Failed to load analytics', err);
    } finally {
      setLoading(false);
    }
  };

  // Single subject per page now, so this just groups straight into the
  // topic hierarchy rather than a subject -> hierarchy nesting.
  // Only average topics the student has actually attempted at least once -
  // spec_topics the student has never practiced come back from the API with
  // mastery_score defaulted to 0.0 (COALESCE, not a real score), and
  // including those as zeros in the average was dragging a group's overall
  // mastery far below what the student was actually scoring on the topics
  // they had practiced (e.g. near-full marks on 2 attempted sub-topics still
  // showing a near-0% group average because 20 sibling sub-topics were
  // never attempted).
  // null (not 0) when nothing in the group has been attempted yet - a
  // group average of 0 reads as "0% mastery", which is a different claim
  // from "no data yet" and was showing for every topic the student simply
  // hadn't reached.
  const avg = (arr: any[]): number | null => {
    const attempted = arr.filter(t => t.attempts_count > 0);
    return attempted.length ? attempted.reduce((s, t) => s + t.mastery_score, 0) / attempted.length : null;
  };
  const subTopics = useMemo(() => {
    return groupByTopicHierarchy(data?.topic_mastery || []).map((g: any) => ({
      label: g.label,
      avg: avg(g.topics),
      topics: g.topics,
    }));
  }, [data]);

  if (loading) {
    return <Spinner label="Building your Master Student Profile…" />;
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem' }}>
      <div style={{ marginBottom: '2rem' }}>
        <Link to={`/app/subject/${subjectKey}`} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '0.75rem' }}>
          <ArrowLeft size={16} aria-hidden="true" /> Back to {subject}
        </Link>
        <h1>{subject} analytics</h1>
        <p style={{ color: 'var(--text-secondary)' }}>
          {level} {exam_board} - a real-time picture of where you stand: mastery per topic, memory decay over time, and misconceptions we're still watching.
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
          {subTopics.length === 0 ? (
            <EmptyState
              icon={<LineChart size={40} strokeWidth={1.5} />}
              title="Nothing to show yet"
              description="Answer a few practice questions and your topic-by-topic mastery will build up here."
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '1rem' }}>
              {subTopics.map((sub: any) => {
                const subPct = sub.avg == null ? null : Math.round(sub.avg * 100);
                return (
                  <details key={sub.label} style={{
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
                      <span>{sub.label}</span>
                      {subPct == null ? (
                        <span style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>Not attempted yet</span>
                      ) : (
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: masteryColor(subPct), fontSize: '0.9rem' }}>
                          <MasteryIcon scorePct={subPct} />
                          {subPct}% avg
                        </span>
                      )}
                    </summary>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', marginTop: '0.75rem' }}>
                      {sub.topics.map((topic: any) => {
                        const notAttempted = topic.attempts_count === 0;
                        const scorePct = Math.round(topic.mastery_score * 100);
                        const badgeColor = notAttempted ? 'var(--text-muted)' : masteryColor(scorePct);
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
                                {notAttempted ? 'Not attempted' : (
                                  <>
                                    <MasteryIcon scorePct={scorePct} />
                                    {scorePct}% mastery
                                  </>
                                )}
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
                                width: `${notAttempted ? 0 : scorePct}%`,
                                height: '100%',
                                background: badgeColor,
                                transition: 'width 0.5s ease'
                              }} />
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.4rem' }}>
                              <span>Attempts: {topic.attempts_count}</span>
                              {!notAttempted && <span>Decay score: {Math.round(topic.decay_score * 100)}%</span>}
                            </div>
                          </div>
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
            <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '-0.5rem', marginBottom: '0.5rem' }}>
              Across all your subjects, not just {subject}.
            </p>
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
