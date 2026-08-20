import React, { useState, useRef, useEffect } from 'react';
import api from './api';
import type { Question } from './types';

interface ExamCanvasProps {
  question: Question;
  onAnswerSubmitted?: (result: any) => void;
  onNext?: () => void;
}

export default function ExamCanvas({ question, onAnswerSubmitted, onNext }: ExamCanvasProps) {
  const [mode, setMode] = useState<'typed' | 'canvas'>('typed');
  const [answerText, setAnswerText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [markingResult, setMarkingResult] = useState<any>(null);
  const [showMarkScheme, setShowMarkScheme] = useState(false);
  const [officialScheme, setOfficialScheme] = useState<string | null>(null);

  // Canvas drawing state
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [isDrawing, setIsDrawing] = useState(false);
  const [tool, setTool] = useState<'pen' | 'highlighter' | 'eraser'>('pen');
  const [color, setColor] = useState('#6366f1');
  const [history, setHistory] = useState<ImageData[]>([]);

  useEffect(() => {
    // Reset state on question change
    setAnswerText('');
    setMarkingResult(null);
    setShowMarkScheme(false);
    setOfficialScheme(null);
    clearCanvas();
  }, [question.id]);

  const clearCanvas = () => {
    const canvas = canvasRef.current;
    if (canvas) {
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        setHistory([]);
      }
    }
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Save history for undo
    setHistory(prev => [...prev, ctx.getImageData(0, 0, canvas.width, canvas.height)]);

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    ctx.beginPath();
    ctx.moveTo(x, y);
    setIsDrawing(true);
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDrawing) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    if (tool === 'eraser') {
      ctx.strokeStyle = '#0f172a';
      ctx.lineWidth = 18;
      ctx.globalAlpha = 1.0;
    } else if (tool === 'highlighter') {
      ctx.strokeStyle = '#facc15';
      ctx.lineWidth = 14;
      ctx.globalAlpha = 0.35;
    } else {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.5;
      ctx.globalAlpha = 1.0;
    }

    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.lineTo(x, y);
    ctx.stroke();
  };

  const handleMouseUp = () => {
    setIsDrawing(false);
  };

  const handleUndo = () => {
    const canvas = canvasRef.current;
    if (!canvas || history.length === 0) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const previous = history[history.length - 1];
    ctx.putImageData(previous, 0, 0);
    setHistory(prev => prev.slice(0, prev.length - 1));
  };

  const handleSubmit = async () => {
    if (!answerText.trim() && mode === 'typed') return;
    setSubmitting(true);

    try {
      let canvasDataUrl: string | undefined = undefined;
      if (mode === 'canvas' && canvasRef.current) {
        canvasDataUrl = canvasRef.current.toDataURL('image/png');
      }

      const res = await api.post('/feedback/submit', {
        question_id: question.id,
        answer_text: answerText || 'Answer submitted via drawing canvas',
        answer_image_url: canvasDataUrl
      });

      setMarkingResult(res.data);
      if (onAnswerSubmitted) {
        onAnswerSubmitted(res.data);
      }
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Failed to submit answer');
    } finally {
      setSubmitting(false);
    }
  };

  const lineCount = Math.max(3, question.mark_value * 2);

  return (
    <div className="exam-canvas-wrapper" style={{
      background: '#ffffff',
      color: '#0f172a',
      borderRadius: '12px',
      padding: '2.5rem',
      boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5)',
      fontFamily: "'Crimson Pro', 'Georgia', serif",
      border: '1px solid #e2e8f0',
      maxWidth: '850px',
      margin: '0 auto'
    }}>
      {/* Exam Header */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        borderBottom: '2px solid #0f172a',
        paddingBottom: '0.75rem',
        marginBottom: '1.5rem'
      }}>
        <div>
          <span style={{ fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.1em', fontWeight: 700, color: '#64748b' }}>
            {question.exam_board || 'AQA'} GCSE {question.subject || 'Biology'}
          </span>
          {question.spec_code && (
            <span style={{ marginLeft: '0.75rem', background: '#f1f5f9', color: '#475569', padding: '0.2rem 0.5rem', borderRadius: '4px', fontSize: '0.8rem', fontFamily: 'sans-serif' }}>
              Spec {question.spec_code}: {question.topic_title}
            </span>
          )}
        </div>
        <div style={{ fontWeight: 700, fontSize: '1rem', color: '#0f172a' }}>
          [{question.mark_value} {question.mark_value === 1 ? 'mark' : 'marks'}]
        </div>
      </div>

      {/* Question Text */}
      <div style={{ fontSize: '1.25rem', lineHeight: '1.7', marginBottom: '1.5rem' }}>
        <strong style={{ fontSize: '1.35rem', marginRight: '0.5rem', fontFamily: 'sans-serif' }}>
          {question.question_number}
        </strong>
        {question.question_text}
      </div>

      {/* Embedded Diagrams / Figures */}
      {(() => {
        const imagesList = Array.isArray(question.images)
          ? question.images
          : (typeof question.images === 'string'
              ? (() => { try { return JSON.parse(question.images); } catch { return []; } })()
              : []);
        if (imagesList.length === 0) return null;
        return (
          <div style={{ marginBottom: '2rem', textAlign: 'center', background: '#f8fafc', padding: '1rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
            {imagesList.map((img: any, i: number) => (
              <div key={i} style={{ display: 'inline-block', margin: '0.5rem' }}>
                <img
                  src={img.url}
                  alt={img.description || `Figure for Q${question.question_number}`}
                  style={{ maxWidth: '100%', maxHeight: '350px', borderRadius: '6px', border: '1px solid #cbd5e1' }}
                />
                {img.description && (
                  <div style={{ fontSize: '0.85rem', color: '#64748b', marginTop: '0.4rem', fontStyle: 'italic' }}>
                    {img.description}
                  </div>
                )}
              </div>
            ))}
          </div>
        );
      })()}

      {/* Input Mode Switcher & Tools */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        background: '#f8fafc',
        padding: '0.5rem 0.75rem',
        borderRadius: '8px',
        marginBottom: '1rem',
        fontFamily: 'sans-serif',
        fontSize: '0.85rem'
      }}>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            onClick={() => setMode('typed')}
            style={{
              padding: '0.35rem 0.75rem',
              borderRadius: '6px',
              border: 'none',
              background: mode === 'typed' ? '#0f172a' : 'transparent',
              color: mode === 'typed' ? '#ffffff' : '#64748b',
              cursor: 'pointer',
              fontWeight: 600
            }}>
            ⌨️ Typed Answer
          </button>
          <button
            onClick={() => setMode('canvas')}
            style={{
              padding: '0.35rem 0.75rem',
              borderRadius: '6px',
              border: 'none',
              background: mode === 'canvas' ? '#0f172a' : 'transparent',
              color: mode === 'canvas' ? '#ffffff' : '#64748b',
              cursor: 'pointer',
              fontWeight: 600
            }}>
            ✍️ Ink Canvas
          </button>
        </div>

        {mode === 'canvas' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <button
              onClick={() => setTool('pen')}
              style={{
                background: tool === 'pen' ? '#e2e8f0' : 'transparent',
                border: '1px solid #cbd5e1',
                borderRadius: '4px',
                padding: '0.2rem 0.5rem',
                cursor: 'pointer'
              }}>
              ✒️ Pen
            </button>
            <button
              onClick={() => setTool('highlighter')}
              style={{
                background: tool === 'highlighter' ? '#fef08a' : 'transparent',
                border: '1px solid #cbd5e1',
                borderRadius: '4px',
                padding: '0.2rem 0.5rem',
                cursor: 'pointer'
              }}>
              🖍️ Highlight
            </button>
            <button
              onClick={() => setTool('eraser')}
              style={{
                background: tool === 'eraser' ? '#e2e8f0' : 'transparent',
                border: '1px solid #cbd5e1',
                borderRadius: '4px',
                padding: '0.2rem 0.5rem',
                cursor: 'pointer'
              }}>
              🧹 Eraser
            </button>
            <button
              onClick={handleUndo}
              style={{
                background: 'transparent',
                border: '1px solid #cbd5e1',
                borderRadius: '4px',
                padding: '0.2rem 0.5rem',
                cursor: 'pointer'
              }}>
              ↩️ Undo
            </button>
            <button
              onClick={clearCanvas}
              style={{
                background: 'transparent',
                border: '1px solid #cbd5e1',
                borderRadius: '4px',
                padding: '0.2rem 0.5rem',
                cursor: 'pointer',
                color: '#ef4444'
              }}>
              🗑️ Clear
            </button>
          </div>
        )}
      </div>

      {/* Answer Workspace Area */}
      <div style={{ position: 'relative', minHeight: `${lineCount * 36}px`, marginBottom: '1.5rem' }}>
        {/* Lined Paper Lines Background */}
        <div style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundImage: 'repeating-linear-gradient(transparent, transparent 35px, #94a3b8 35px, #94a3b8 36px)',
          pointerEvents: 'none',
          opacity: 0.6
        }} />

        {mode === 'typed' ? (
          <textarea
            value={answerText}
            onChange={(e) => setAnswerText(e.target.value)}
            disabled={!!markingResult}
            placeholder="Write your answer here..."
            style={{
              width: '100%',
              minHeight: `${lineCount * 36}px`,
              background: 'transparent',
              border: 'none',
              outline: 'none',
              resize: 'vertical',
              fontSize: '1.15rem',
              lineHeight: '36px',
              padding: '0 0.5rem',
              color: '#0f172a',
              fontFamily: "'Crimson Pro', 'Georgia', serif",
              zIndex: 1,
              position: 'relative'
            }}
          />
        ) : (
          <canvas
            ref={canvasRef}
            width={770}
            height={lineCount * 36}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
            style={{
              position: 'relative',
              zIndex: 1,
              cursor: tool === 'eraser' ? 'cell' : 'crosshair',
              width: '100%',
              background: 'transparent'
            }}
          />
        )}
      </div>

      {/* Marking Feedback Overlay */}
      {markingResult && (
        <div style={{
          marginTop: '1.5rem',
          padding: '1.25rem',
          borderRadius: '8px',
          background: markingResult.is_full_marks ? '#f0fdf4' : '#fff1f2',
          border: `1px solid ${markingResult.is_full_marks ? '#86efac' : '#fecdd3'}`,
          fontFamily: 'sans-serif'
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span style={{ fontSize: '1.5rem' }}>
                {markingResult.is_full_marks ? '✅' : '❌'}
              </span>
              <strong style={{ fontSize: '1.1rem', color: markingResult.is_full_marks ? '#166534' : '#9f1239' }}>
                {markingResult.marks_awarded} / {markingResult.marks_possible} Marks Awarded
              </strong>
              <span style={{
                background: markingResult.marked_by === 'dsl' ? '#e0e7ff' : '#fef3c7',
                color: markingResult.marked_by === 'dsl' ? '#3730a3' : '#92400e',
                fontSize: '0.75rem',
                padding: '0.15rem 0.4rem',
                borderRadius: '4px',
                fontWeight: 600
              }}>
                {markingResult.marked_by === 'dsl' ? 'Deterministic DSL (<10ms)' : 'Spec Model (<300ms)'}
              </span>
            </div>
            <button
              onClick={() => setShowMarkScheme(!showMarkScheme)}
              style={{
                background: 'transparent',
                border: '1px solid #94a3b8',
                borderRadius: '4px',
                padding: '0.25rem 0.6rem',
                fontSize: '0.8rem',
                cursor: 'pointer',
                color: '#334155'
              }}>
              {showMarkScheme ? 'Hide Mark Scheme' : '📖 View Mark Scheme'}
            </button>
          </div>

          <p style={{ margin: 0, color: '#334155', lineHeight: '1.5', fontSize: '0.95rem' }}>
            {markingResult.feedback_text}
          </p>

          {/* Missed Points Chips */}
          {markingResult.missed_points && markingResult.missed_points.length > 0 && (
            <div style={{ marginTop: '0.75rem' }}>
              <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#be123c', textTransform: 'uppercase' }}>Missed Points:</span>
              <ul style={{ margin: '0.25rem 0 0 1.25rem', padding: 0, color: '#475569', fontSize: '0.9rem' }}>
                {markingResult.missed_points.map((p: string, i: number) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Misconception Warnings */}
          {markingResult.misconception_tags && markingResult.misconception_tags.length > 0 && (
            <div style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
              {markingResult.misconception_tags.map((tag: string, i: number) => (
                <span key={i} style={{
                  background: '#ffe4e6',
                  color: '#9f1239',
                  border: '1px solid #fecdd3',
                  borderRadius: '12px',
                  padding: '0.2rem 0.6rem',
                  fontSize: '0.75rem',
                  fontWeight: 600
                }}>
                  ⚠️ Misconception Detected: {tag}
                </span>
              ))}
            </div>
          )}

          {/* Official Mark Scheme Reveal */}
          {showMarkScheme && (
            <div style={{
              marginTop: '1rem',
              padding: '0.75rem',
              background: '#f8fafc',
              borderRadius: '6px',
              borderLeft: '4px solid #0f172a',
              fontSize: '0.85rem'
            }}>
              <strong style={{ color: '#0f172a' }}>Official Exam Board Guidance:</strong>
              <div style={{ color: '#334155', marginTop: '0.25rem' }}>
                Full marks are awarded for identifying required key terms and scientific reasoning aligned with specification code.
              </div>
            </div>
          )}
        </div>
      )}

      {/* Action Footer */}
      <div style={{
        marginTop: '1.5rem',
        display: 'flex',
        justifyContent: 'flex-end',
        gap: '0.75rem',
        fontFamily: 'sans-serif'
      }}>
        {!markingResult ? (
          <button
            onClick={handleSubmit}
            disabled={submitting || (!answerText.trim() && mode === 'typed')}
            style={{
              background: 'linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%)',
              color: '#ffffff',
              border: 'none',
              borderRadius: '8px',
              padding: '0.65rem 1.5rem',
              fontWeight: 600,
              fontSize: '0.95rem',
              cursor: 'pointer',
              boxShadow: '0 4px 6px -1px rgba(79, 70, 229, 0.3)'
            }}>
            {submitting ? 'Marking Instant Feedback...' : 'Submit & Mark Question'}
          </button>
        ) : (
          onNext && (
            <button
              onClick={onNext}
              style={{
                background: '#0f172a',
                color: '#ffffff',
                border: 'none',
                borderRadius: '8px',
                padding: '0.65rem 1.5rem',
                fontWeight: 600,
                fontSize: '0.95rem',
                cursor: 'pointer'
              }}>
              Next Question →
            </button>
          )
        )}
      </div>
    </div>
  );
}
