import React, { useState, useEffect } from 'react';
import api from './api';
import ExamCanvas from './ExamCanvas';
import type { Question } from './types';

export default function AdaptivePractice() {
  const [questions, setQuestions] = useState<Question[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [focusReason, setFocusReason] = useState<any>(null);
  const [sessionCompleted, setSessionCompleted] = useState(false);
  const [sessionScore, setSessionScore] = useState({ earned: 0, possible: 0 });

  useEffect(() => {
    fetchAdaptiveQueue();
  }, []);

  const fetchAdaptiveQueue = async () => {
    setLoading(true);
    try {
      const res = await api.get('/generate/adaptive-queue');
      setQuestions(res.data.queue);
      setFocusReason(res.data.focus_reason);
      setCurrentIndex(0);
      setSessionCompleted(false);
      setSessionScore({ earned: 0, possible: 0 });
    } catch (err) {
      console.error('Failed to load practice queue', err);
    } finally {
      setLoading(false);
    }
  };

  const handleAnswerSubmitted = (result: any) => {
    setSessionScore(prev => ({
      earned: prev.earned + result.marks_awarded,
      possible: prev.possible + result.marks_possible
    }));
  };

  const handleNext = () => {
    if (currentIndex + 1 < questions.length) {
      setCurrentIndex(prev => prev + 1);
    } else {
      setSessionCompleted(true);
    }
  };

  if (loading) {
    return (
      <div className="container" style={{ textAlign: 'center', marginTop: '4rem' }}>
        <h2>Loading your personalized practice queue...</h2>
        <p style={{ color: '#94a3b8' }}>Consulting Master Student Profile & Ebbinghaus decay curve...</p>
      </div>
    );
  }

  if (sessionCompleted) {
    const pct = sessionScore.possible > 0 ? Math.round((sessionScore.earned / sessionScore.possible) * 100) : 100;
    return (
      <div className="container" style={{ maxWidth: '650px', marginTop: '3rem', textAlign: 'center' }}>
        <div className="card" style={{ padding: '3rem' }}>
          <div style={{ fontSize: '3.5rem', marginBottom: '1rem' }}>🎉</div>
          <h2 style={{ marginBottom: '0.5rem' }}>Practice Session Completed!</h2>
          <p style={{ color: '#94a3b8', marginBottom: '2rem' }}>
            Your Master Student Profile has been updated with real-time EWMA mastery scores.
          </p>

          <div style={{
            display: 'flex',
            justifyContent: 'center',
            gap: '2rem',
            background: 'rgba(255, 255, 255, 0.05)',
            padding: '1.5rem',
            borderRadius: '12px',
            marginBottom: '2rem'
          }}>
            <div>
              <div style={{ fontSize: '2rem', fontWeight: 800, color: '#6366f1' }}>
                {sessionScore.earned} / {sessionScore.possible}
              </div>
              <div style={{ fontSize: '0.85rem', color: '#94a3b8' }}>Marks Earned</div>
            </div>
            <div>
              <div style={{ fontSize: '2rem', fontWeight: 800, color: pct >= 70 ? '#10b981' : '#f59e0b' }}>
                {pct}%
              </div>
              <div style={{ fontSize: '0.85rem', color: '#94a3b8' }}>Accuracy</div>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center' }}>
            <button onClick={fetchAdaptiveQueue} className="btn btn-primary">
              Start Another Queue
            </button>
            <a href="/analytics" className="btn btn-outline">
              View Analytics Heatmap
            </a>
          </div>
        </div>
      </div>
    );
  }

  const currentQ = questions[currentIndex];

  return (
    <div className="container" style={{ marginTop: '2rem', marginBottom: '4rem' }}>
      {/* Session Progress Header */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        maxWidth: '850px',
        margin: '0 auto 1.5rem auto'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <span style={{
            background: '#6366f1',
            color: '#ffffff',
            padding: '0.2rem 0.6rem',
            borderRadius: '6px',
            fontSize: '0.85rem',
            fontWeight: 700
          }}>
            Question {currentIndex + 1} of {questions.length}
          </span>
          {focusReason && focusReason.active_misconceptions_count > 0 && (
            <span style={{
              background: 'rgba(239, 68, 68, 0.15)',
              color: '#f87171',
              padding: '0.2rem 0.6rem',
              borderRadius: '6px',
              fontSize: '0.8rem'
            }}>
              🎯 Targeting {focusReason.active_misconceptions_count} active misconception(s)
            </span>
          )}
        </div>

        <div style={{ color: '#94a3b8', fontSize: '0.9rem' }}>
          Session Score: <strong style={{ color: '#ffffff' }}>{sessionScore.earned} / {sessionScore.possible}</strong>
        </div>
      </div>

      {/* The Exam Paper Canvas */}
      {currentQ ? (
        <ExamCanvas
          question={currentQ}
          onAnswerSubmitted={handleAnswerSubmitted}
          onNext={handleNext}
        />
      ) : (
        <div className="card text-center" style={{ maxWidth: '600px', margin: '0 auto' }}>
          <p>No questions currently available in this queue.</p>
        </div>
      )}
    </div>
  );
}
