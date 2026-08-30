import React, { useState, useRef, useEffect } from 'react';
import api, { extractErrorMessage } from './api';
import type { Question, AnswerOption, GridRow } from './types';
import { formatUnits } from './formatUnits';
import Markdown from './Markdown';
import MathInput from './MathInput';
import { Pen, Highlighter, Eraser, Undo2, Trash2, CheckCircle2, XCircle, AlertCircle, ArrowRight } from 'lucide-react';

function parseJsonMaybe<T>(value: T | string | null | undefined, fallback: T): T {
  if (value == null) return fallback;
  if (typeof value !== 'string') return value;
  try { return JSON.parse(value); } catch { return fallback; }
}

interface ExamCanvasProps {
  questions: Question[];
  onAnswerSubmitted?: (result: any) => void;
  onNext?: () => void;
  nextLabel?: string;
}

type Mode = 'typed' | 'canvas';
type Tool = 'pen' | 'highlighter' | 'eraser';

export default function ExamCanvas({ questions, onAnswerSubmitted, onNext, nextLabel = 'Next question' }: ExamCanvasProps) {
  const groupKey = questions.map(q => q.id).join('|');

  const [modes, setModes] = useState<Record<string, Mode>>({});
  const [answerTexts, setAnswerTexts] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState<Record<string, boolean>>({});
  const [markingResults, setMarkingResults] = useState<Record<string, any>>({});
  const [canvasSnapshots, setCanvasSnapshots] = useState<Record<string, string>>({});
  const [previousAnswers, setPreviousAnswers] = useState<Record<string, Question['previous_answer']>>({});
  const [showMarkScheme, setShowMarkScheme] = useState<Record<string, boolean>>({});
  const [tools, setTools] = useState<Record<string, Tool>>({});
  const [history, setHistory] = useState<Record<string, ImageData[]>>({});
  const [isDrawing, setIsDrawing] = useState<Record<string, boolean>>({});
  const [submitErrors, setSubmitErrors] = useState<Record<string, string>>({});

  const canvasRefs = useRef<Record<string, HTMLCanvasElement | null>>({});

  useEffect(() => {
    // Reset all state whenever the question group changes, then restore
    // whatever the student already submitted for these questions (if
    // anything) so re-opening a partially/fully done question shows their
    // prior work instead of a blank slate.
    setSubmitting({});
    setShowMarkScheme({});
    setTools({});
    setHistory({});
    setIsDrawing({});
    canvasRefs.current = {};

    const initialModes: Record<string, Mode> = {};
    const initialAnswers: Record<string, string> = {};
    const initialResults: Record<string, any> = {};
    const initialSnapshots: Record<string, string> = {};
    const initialPrevious: Record<string, Question['previous_answer']> = {};

    questions.forEach(q => {
      const prev = q.previous_answer;
      if (!prev) return;
      initialPrevious[q.id] = prev;
      if (prev.answer_text) initialAnswers[q.id] = prev.answer_text;
      if (prev.answer_image_url) {
        initialSnapshots[q.id] = prev.answer_image_url;
        initialModes[q.id] = 'canvas';
      }
      // Already scored full marks: treat as done, show it like a just-marked
      // answer rather than re-prompting. Anything less than full marks stays
      // editable (below) so the student can review and re-attempt it.
      if (prev.marks_awarded === prev.marks_possible) {
        initialResults[q.id] = {
          marks_awarded: prev.marks_awarded,
          marks_possible: prev.marks_possible,
          feedback_text: prev.feedback_text,
          missed_points: parseJsonMaybe<string[]>(prev.missed_points, []),
          misconception_tags: parseJsonMaybe<string[]>(prev.misconception_tags, []),
          marked_by: prev.marked_by,
          is_full_marks: true
        };
      }
    });

    setModes(initialModes);
    setAnswerTexts(initialAnswers);
    setMarkingResults(initialResults);
    setCanvasSnapshots(initialSnapshots);
    setPreviousAnswers(initialPrevious);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupKey]);

  const clearCanvas = (qid: string) => {
    const canvas = canvasRefs.current[qid];
    if (canvas) {
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        setHistory(prev => ({ ...prev, [qid]: [] }));
      }
    }
  };

  // Core drawing logic shared by mouse and touch input, keyed off raw
  // viewport coordinates so both input types can drive the same canvas.
  const startStroke = (qid: string, clientX: number, clientY: number) => {
    const canvas = canvasRefs.current[qid];
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    setHistory(prev => ({ ...prev, [qid]: [...(prev[qid] || []), ctx.getImageData(0, 0, canvas.width, canvas.height)] }));

    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const x = (clientX - rect.left) * scaleX;
    const y = (clientY - rect.top) * scaleY;

    ctx.beginPath();
    ctx.moveTo(x, y);
    setIsDrawing(prev => ({ ...prev, [qid]: true }));
  };

  const continueStroke = (qid: string, clientX: number, clientY: number) => {
    if (!isDrawing[qid]) return;
    const canvas = canvasRefs.current[qid];
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const x = (clientX - rect.left) * scaleX;
    const y = (clientY - rect.top) * scaleY;

    const tool = tools[qid] || 'pen';
    if (tool === 'eraser') {
      ctx.strokeStyle = '#0f172a';
      ctx.lineWidth = 18;
      ctx.globalAlpha = 1.0;
    } else if (tool === 'highlighter') {
      ctx.strokeStyle = '#facc15';
      ctx.lineWidth = 14;
      ctx.globalAlpha = 0.35;
    } else {
      ctx.strokeStyle = '#dc2626';
      ctx.lineWidth = 2.5;
      ctx.globalAlpha = 1.0;
    }

    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.lineTo(x, y);
    ctx.stroke();
  };

  const endStroke = (qid: string) => {
    setIsDrawing(prev => ({ ...prev, [qid]: false }));
  };

  const handleMouseDown = (qid: string) => (e: React.MouseEvent<HTMLCanvasElement>) => {
    startStroke(qid, e.clientX, e.clientY);
  };

  const handleMouseMove = (qid: string) => (e: React.MouseEvent<HTMLCanvasElement>) => {
    continueStroke(qid, e.clientX, e.clientY);
  };

  const handleMouseUp = (qid: string) => () => {
    endStroke(qid);
  };

  // Touch equivalents so the drawing canvas is usable on tablets/phones,
  // not just with a mouse. preventDefault stops the page from scrolling
  // while the student is drawing.
  const handleTouchStart = (qid: string) => (e: React.TouchEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const touch = e.touches[0];
    if (touch) startStroke(qid, touch.clientX, touch.clientY);
  };

  const handleTouchMove = (qid: string) => (e: React.TouchEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const touch = e.touches[0];
    if (touch) continueStroke(qid, touch.clientX, touch.clientY);
  };

  const handleTouchEnd = (qid: string) => (e: React.TouchEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    endStroke(qid);
  };

  const handleUndo = (qid: string) => {
    const canvas = canvasRefs.current[qid];
    const qHistory = history[qid] || [];
    if (!canvas || qHistory.length === 0) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const previous = qHistory[qHistory.length - 1];
    ctx.putImageData(previous, 0, 0);
    setHistory(prev => ({ ...prev, [qid]: qHistory.slice(0, qHistory.length - 1) }));
  };

  const toggleMultiSelect = (qid: string, key: string) => {
    const current = new Set((answerTexts[qid] || '').split(',').map(s => s.trim()).filter(Boolean));
    if (current.has(key)) current.delete(key);
    else current.add(key);
    setAnswerTexts(prev => ({ ...prev, [qid]: Array.from(current).sort().join(', ') }));
  };

  const getGridSelections = (qid: string, rows: GridRow[]): string[] => {
    const raw = answerTexts[qid] || '';
    const parts = raw ? raw.split(', ') : [];
    return rows.map((_, i) => parts[i] || '');
  };

  const setGridSelection = (qid: string, rows: GridRow[], rowIndex: number, value: string) => {
    const current = getGridSelections(qid, rows);
    current[rowIndex] = value;
    setAnswerTexts(prev => ({ ...prev, [qid]: current.join(', ') }));
  };

  const handleSubmit = async (question: Question) => {
    const qid = question.id;
    const mode = modes[qid] || 'typed';
    const answerText = answerTexts[qid] || '';
    if (!answerText.trim() && mode === 'typed') return;

    setSubmitting(prev => ({ ...prev, [qid]: true }));
    setSubmitErrors(prev => ({ ...prev, [qid]: '' }));
    try {
      let canvasDataUrl: string | undefined = undefined;
      if (mode === 'canvas' && canvasRefs.current[qid]) {
        canvasDataUrl = canvasRefs.current[qid]!.toDataURL('image/png');
        setCanvasSnapshots(prev => ({ ...prev, [qid]: canvasDataUrl! }));
      }

      const res = await api.post('/feedback/submit', {
        question_id: qid,
        answer_text: answerText || 'Answer submitted via drawing canvas',
        answer_image_url: canvasDataUrl
      });

      setMarkingResults(prev => ({ ...prev, [qid]: res.data }));
      if (onAnswerSubmitted) {
        onAnswerSubmitted(res.data);
      }
    } catch (err: any) {
      setSubmitErrors(prev => ({ ...prev, [qid]: extractErrorMessage(err, 'Failed to submit answer') }));
    } finally {
      setSubmitting(prev => ({ ...prev, [qid]: false }));
    }
  };

  const allAnswered = questions.length > 0 && questions.every(q => !!markingResults[q.id]);
  const first = questions[0];

  return (
    <div className="exam-canvas-wrapper" style={{
      background: '#ffffff',
      color: '#0f172a',
      borderRadius: '12px',
      boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5)',
      fontFamily: "'Crimson Pro', 'Georgia', serif",
      border: '1px solid #e2e8f0',
      maxWidth: '850px',
      margin: '0 auto'
    }}>
      {/* Shared Exam Header */}
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
            {first.exam_board || 'AQA'} GCSE {first.subject || 'Biology'}
          </span>
        </div>
        <div style={{ fontWeight: 700, fontSize: '1rem', color: '#0f172a' }}>
          [{questions.reduce((s, q) => s + q.mark_value, 0)} marks total]
        </div>
      </div>

      {questions.map((question, idx) => {
        const qid = question.id;
        const mode = modes[qid] || 'typed';
        const tool = tools[qid] || 'pen';
        const answerText = answerTexts[qid] || '';
        const markingResult = markingResults[qid];
        const isSubmitting = !!submitting[qid];
        const lineCount = Math.max(3, question.mark_value * 2);
        const answerType = question.answer_type || 'written';
        const answerOptions: any = typeof question.answer_options === 'string'
          ? (() => { try { return JSON.parse(question.answer_options as any); } catch { return null; } })()
          : question.answer_options;

        const imagesList = Array.isArray(question.images)
          ? question.images
          : (typeof question.images === 'string'
              ? (() => { try { return JSON.parse(question.images as any); } catch { return []; } })()
              : []);

        // Shared header (topic tag, question text, mark value, images) used
        // by both the practical and normal-input rendering below.
        const questionHeader = (
          <>
            {question.spec_code && (
              <div style={{ marginBottom: '0.5rem' }}>
                <span style={{ background: '#f1f5f9', color: '#475569', padding: '0.2rem 0.5rem', borderRadius: '4px', fontSize: '0.8rem', fontFamily: 'sans-serif' }}>
                  Spec {question.spec_code}: {question.topic_title}
                </span>
              </div>
            )}

            <div style={{ fontSize: '1.25rem', lineHeight: '1.7', marginBottom: '1rem', display: 'flex', justifyContent: 'space-between', gap: '1rem' }}>
              <div style={{ flex: 1 }}>
                <strong style={{ fontSize: '1.35rem', marginRight: '0.5rem', fontFamily: 'sans-serif' }}>
                  {question.question_number}
                </strong>
                <Markdown>{question.question_text}</Markdown>
              </div>
              <div style={{ fontWeight: 700, fontSize: '0.95rem', color: '#64748b', whiteSpace: 'nowrap', fontFamily: 'sans-serif' }}>
                [{question.mark_value} {question.mark_value === 1 ? 'mark' : 'marks'}]
              </div>
            </div>

            {imagesList.length > 0 && (
              <div style={{ marginBottom: '1.5rem', textAlign: 'center', background: '#f8fafc', padding: '1rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                {imagesList.map((img: any, i: number) => (
                  <div key={i} style={{ display: 'inline-block', margin: '0.5rem' }}>
                    <img
                      src={img.url}
                      alt={img.caption || `Figure for Q${question.question_number}`}
                      style={{ maxWidth: '100%', maxHeight: '350px', borderRadius: '6px', border: '1px solid #cbd5e1' }}
                    />
                    {img.caption && (
                      <div style={{ marginTop: '0.4rem', fontSize: '0.85rem', fontWeight: 600, color: '#475569', fontFamily: 'sans-serif' }}>
                        {img.caption}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        );

        // Draw/complete/label/plot questions have no text/numeric answer to
        // grade - there's nothing meaningful to type in, so skip the whole
        // input/submit/marking flow and just point the student at paper,
        // letting them self-check against the mark scheme instead.
        if (answerType === 'practical') {
          return (
            <div key={qid} style={{ marginBottom: idx < questions.length - 1 ? '2.5rem' : 0, paddingBottom: idx < questions.length - 1 ? '2rem' : 0, borderBottom: idx < questions.length - 1 ? '1px dashed #cbd5e1' : 'none' }}>
              {questionHeader}

              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.6rem',
                background: '#fffbeb',
                border: '1px solid #fde68a',
                borderRadius: '8px',
                padding: '0.75rem 1rem',
                marginBottom: '1rem',
                fontFamily: 'sans-serif',
                fontSize: '0.9rem',
                color: '#92400e'
              }}>
                <Pen size={16} aria-hidden="true" />
                <span>This one's for pen and paper. Draw/complete it there, then check your work against the mark scheme below.</span>
              </div>

              <button
                onClick={() => setShowMarkScheme(prev => ({ ...prev, [qid]: !prev[qid] }))}
                style={{
                  background: 'transparent',
                  border: '1px solid #cbd5e1',
                  borderRadius: '6px',
                  padding: '0.5rem 1rem',
                  fontFamily: 'sans-serif',
                  fontSize: '0.9rem',
                  fontWeight: 600,
                  color: '#334155',
                  cursor: 'pointer'
                }}>
                {showMarkScheme[qid] ? 'Hide mark scheme' : 'Show mark scheme'}
              </button>

              {showMarkScheme[qid] && (
                <div style={{ marginTop: '0.75rem', background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '1rem', fontFamily: 'sans-serif', fontSize: '0.95rem' }}>
                  <strong style={{ display: 'block', marginBottom: '0.35rem', color: '#0f172a' }}>Mark scheme</strong>
                  <div style={{ color: '#334155' }}>
                    {question.mark_scheme_text ? (
                      <Markdown>{question.mark_scheme_text}</Markdown>
                    ) : (
                      'No mark scheme was extracted for this question.'
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        }

        return (
          <div key={qid} style={{ marginBottom: idx < questions.length - 1 ? '2.5rem' : 0, paddingBottom: idx < questions.length - 1 ? '2rem' : 0, borderBottom: idx < questions.length - 1 ? '1px dashed #cbd5e1' : 'none' }}>
            {questionHeader}

            {/* Input Mode Switcher & Tools (written answers only) */}
            {!markingResult && answerType === 'written' && (
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
                    onClick={() => setModes(prev => ({ ...prev, [qid]: 'typed' }))}
                    style={{
                      padding: '0.35rem 0.75rem',
                      borderRadius: '6px',
                      border: 'none',
                      background: mode === 'typed' ? '#0f172a' : 'transparent',
                      color: mode === 'typed' ? '#ffffff' : '#64748b',
                      cursor: 'pointer',
                      fontWeight: 600
                    }}>
                    Typed Answer
                  </button>
                  <button
                    onClick={() => setModes(prev => ({ ...prev, [qid]: 'canvas' }))}
                    style={{
                      padding: '0.35rem 0.75rem',
                      borderRadius: '6px',
                      border: 'none',
                      background: mode === 'canvas' ? '#0f172a' : 'transparent',
                      color: mode === 'canvas' ? '#ffffff' : '#64748b',
                      cursor: 'pointer',
                      fontWeight: 600
                    }}>
                    Ink Canvas
                  </button>
                </div>

                {mode === 'canvas' && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
                    <button
                      onClick={() => setTools(prev => ({ ...prev, [qid]: 'pen' }))}
                      aria-pressed={tool === 'pen'}
                      aria-label="Pen"
                      title="Pen"
                      style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', background: tool === 'pen' ? '#e2e8f0' : 'transparent', border: '1px solid #cbd5e1', borderRadius: '4px', padding: '0.25rem 0.5rem', cursor: 'pointer' }}>
                      <Pen size={14} aria-hidden="true" />
                    </button>
                    <button
                      onClick={() => setTools(prev => ({ ...prev, [qid]: 'highlighter' }))}
                      aria-pressed={tool === 'highlighter'}
                      aria-label="Highlighter"
                      title="Highlighter"
                      style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', background: tool === 'highlighter' ? '#fef08a' : 'transparent', border: '1px solid #cbd5e1', borderRadius: '4px', padding: '0.25rem 0.5rem', cursor: 'pointer' }}>
                      <Highlighter size={14} aria-hidden="true" />
                    </button>
                    <button
                      onClick={() => setTools(prev => ({ ...prev, [qid]: 'eraser' }))}
                      aria-pressed={tool === 'eraser'}
                      aria-label="Eraser"
                      title="Eraser"
                      style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', background: tool === 'eraser' ? '#e2e8f0' : 'transparent', border: '1px solid #cbd5e1', borderRadius: '4px', padding: '0.25rem 0.5rem', cursor: 'pointer' }}>
                      <Eraser size={14} aria-hidden="true" />
                    </button>
                    <button
                      onClick={() => handleUndo(qid)}
                      aria-label="Undo last stroke"
                      title="Undo"
                      style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', background: 'transparent', border: '1px solid #cbd5e1', borderRadius: '4px', padding: '0.25rem 0.5rem', cursor: 'pointer' }}>
                      <Undo2 size={14} aria-hidden="true" />
                    </button>
                    <button
                      onClick={() => clearCanvas(qid)}
                      aria-label="Clear drawing"
                      title="Clear"
                      style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', background: 'transparent', border: '1px solid #cbd5e1', borderRadius: '4px', padding: '0.25rem 0.5rem', cursor: 'pointer', color: '#ef4444' }}>
                      <Trash2 size={14} aria-hidden="true" />
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Previous attempt banner - shown when reopening a question that
                was answered but didn't score full marks, so the student
                knows their old answer is prefilled below and can review the
                past feedback before re-attempting. */}
            {!markingResult && previousAnswers[qid] && (
              <div style={{
                marginBottom: '1rem',
                padding: '0.75rem 1rem',
                borderRadius: '8px',
                background: '#fffbeb',
                border: '1px solid #fde68a',
                fontFamily: 'sans-serif',
                fontSize: '0.85rem',
                color: '#92400e'
              }}>
                <div>
                  You previously scored <strong>{previousAnswers[qid]!.marks_awarded} / {previousAnswers[qid]!.marks_possible}</strong> on
                  this question. Your last answer is filled in below - edit and resubmit to try again.
                </div>
                <div style={{ marginTop: '0.5rem', display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
                  <button
                    onClick={() => setShowMarkScheme(prev => ({ ...prev, [`prev-${qid}`]: !prev[`prev-${qid}`] }))}
                    style={{ background: 'transparent', border: '1px solid #d97706', borderRadius: '4px', padding: '0.2rem 0.6rem', fontSize: '0.8rem', cursor: 'pointer', color: '#92400e' }}>
                    {showMarkScheme[`prev-${qid}`] ? 'Hide previous feedback' : 'View previous feedback'}
                  </button>
                  <button
                    onClick={() => setMarkingResults(prev => ({
                      ...prev,
                      [qid]: {
                        marks_awarded: previousAnswers[qid]!.marks_awarded,
                        marks_possible: previousAnswers[qid]!.marks_possible,
                        feedback_text: previousAnswers[qid]!.feedback_text,
                        missed_points: parseJsonMaybe<string[]>(previousAnswers[qid]!.missed_points, []),
                        misconception_tags: parseJsonMaybe<string[]>(previousAnswers[qid]!.misconception_tags, []),
                        marked_by: previousAnswers[qid]!.marked_by,
                        is_full_marks: previousAnswers[qid]!.marks_awarded === previousAnswers[qid]!.marks_possible
                      }
                    }))}
                    style={{ background: 'transparent', border: '1px solid #d97706', borderRadius: '4px', padding: '0.2rem 0.6rem', fontSize: '0.8rem', cursor: 'pointer', color: '#92400e' }}>
                    Keep this score & continue
                  </button>
                </div>
                {showMarkScheme[`prev-${qid}`] && (
                  <div style={{ marginTop: '0.75rem', color: '#78350f' }}>
                    <Markdown>{previousAnswers[qid]!.feedback_text || 'No feedback recorded.'}</Markdown>
                  </div>
                )}
              </div>
            )}

            {/* Answer Workspace Area */}
            {!markingResult && answerType === 'written' && (
              <div style={{ position: 'relative', minHeight: `${lineCount * 36}px`, marginBottom: '1rem' }}>
                <div style={{
                  position: 'absolute',
                  top: 0, left: 0, right: 0, bottom: 0,
                  backgroundImage: 'repeating-linear-gradient(transparent, transparent 35px, #94a3b8 35px, #94a3b8 36px)',
                  pointerEvents: 'none',
                  opacity: 0.6
                }} />

                {mode === 'typed' ? (
                  <textarea
                    value={answerText}
                    onChange={(e) => setAnswerTexts(prev => ({ ...prev, [qid]: e.target.value }))}
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
                    ref={(el) => { canvasRefs.current[qid] = el; }}
                    width={770}
                    height={lineCount * 36}
                    onMouseDown={handleMouseDown(qid)}
                    onMouseMove={handleMouseMove(qid)}
                    onMouseUp={handleMouseUp(qid)}
                    onMouseLeave={handleMouseUp(qid)}
                    onTouchStart={handleTouchStart(qid)}
                    onTouchMove={handleTouchMove(qid)}
                    onTouchEnd={handleTouchEnd(qid)}
                    aria-label={`Handwritten answer area for question ${question.question_number}`}
                    style={{
                      position: 'relative',
                      zIndex: 1,
                      cursor: tool === 'eraser' ? 'cell' : 'crosshair',
                      touchAction: 'none',
                      width: '100%',
                      background: 'transparent'
                    }}
                  />
                )}
              </div>
            )}

            {!markingResult && mode === 'canvas' && previousAnswers[qid] && canvasSnapshots[qid] && (
              <div style={{ marginBottom: '1rem', fontFamily: 'sans-serif' }}>
                <div style={{ fontSize: '0.8rem', color: '#64748b', marginBottom: '0.3rem' }}>Your previous drawing (for reference - the canvas above starts blank):</div>
                <img src={canvasSnapshots[qid]} alt="Your previous handwritten answer" style={{ maxWidth: '100%', maxHeight: '200px', border: '1px solid #cbd5e1', borderRadius: '6px' }} />
              </div>
            )}

            {/* Select / Multi-select MCQ */}
            {!markingResult && (answerType === 'select' || answerType === 'multi_select') && (
              <div style={{ marginBottom: '1.5rem', fontFamily: 'sans-serif' }}>
                {((answerOptions as AnswerOption[]) || []).map(opt => {
                  const isMulti = answerType === 'multi_select';
                  const selectedKeys = new Set(answerText.split(',').map(s => s.trim()).filter(Boolean));
                  const checked = selectedKeys.has(opt.key);
                  return (
                    <label key={opt.key} style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.65rem',
                      padding: '0.65rem 0.9rem',
                      marginBottom: '0.5rem',
                      borderRadius: '8px',
                      border: `1px solid ${checked ? '#4f46e5' : '#e2e8f0'}`,
                      background: checked ? '#eef2ff' : '#ffffff',
                      cursor: 'pointer',
                      fontSize: '1.05rem'
                    }}>
                      <input
                        type={isMulti ? 'checkbox' : 'radio'}
                        name={`q-${qid}`}
                        checked={checked}
                        onChange={() => isMulti
                          ? toggleMultiSelect(qid, opt.key)
                          : setAnswerTexts(prev => ({ ...prev, [qid]: opt.key }))
                        }
                      />
                      <span><strong>{opt.key}:</strong> {opt.text}</span>
                    </label>
                  );
                })}
              </div>
            )}

            {/* Numeric / algebraic answer */}
            {!markingResult && answerType === 'numeric' && (
              <div style={{ marginBottom: '0.5rem', fontFamily: 'sans-serif', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <MathInput
                  value={answerText}
                  onChange={(plain) => setAnswerTexts(prev => ({ ...prev, [qid]: plain }))}
                  placeholder="Enter your answer"
                />
                {(answerOptions as { unit: string | null } | undefined)?.unit && (
                  <span style={{ color: '#475569', fontWeight: 600 }}>
                    {formatUnits((answerOptions as { unit: string }).unit)}
                  </span>
                )}
              </div>
            )}
            {!markingResult && answerType === 'numeric' && (
              <div style={{ marginBottom: '1rem', fontSize: '0.8rem', color: '#94a3b8', fontFamily: 'sans-serif' }}>
                Use ^ for powers (x^2), / for fractions, and type sqrt for a square root.
              </div>
            )}

            {/* Grid select (one choice per row) */}
            {!markingResult && answerType === 'grid_select' && (
              <div style={{ marginBottom: '1.5rem', fontFamily: 'sans-serif', overflowX: 'auto' }}>
                {(() => {
                  const rows = (answerOptions as GridRow[]) || [];
                  const selections = getGridSelections(qid, rows);
                  return (
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                      <tbody>
                        {rows.map((row, rowIndex) => (
                          <tr key={rowIndex} style={{ borderBottom: '1px solid #e2e8f0' }}>
                            <td style={{ padding: '0.6rem 0.5rem', fontSize: '1rem' }}>{row.statement}</td>
                            {row.options.map(opt => (
                              <td key={opt} style={{ padding: '0.6rem 0.75rem', textAlign: 'center', whiteSpace: 'nowrap' }}>
                                <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', cursor: 'pointer', fontSize: '0.95rem' }}>
                                  <input
                                    type="radio"
                                    name={`q-${qid}-row-${rowIndex}`}
                                    checked={selections[rowIndex] === opt}
                                    onChange={() => setGridSelection(qid, rows, rowIndex, opt)}
                                  />
                                  {opt}
                                </label>
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  );
                })()}
              </div>
            )}

            {/* Marking Feedback Overlay */}
            {markingResult && (
              <div style={{
                marginBottom: '1rem',
                padding: '1.25rem',
                borderRadius: '8px',
                background: markingResult.is_full_marks ? '#f0fdf4' : '#fff1f2',
                border: `1px solid ${markingResult.is_full_marks ? '#86efac' : '#fecdd3'}`,
                fontFamily: 'sans-serif'
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem', flexWrap: 'wrap', gap: '0.5rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    {markingResult.is_full_marks ? (
                      <CheckCircle2 size={18} aria-hidden="true" color="#166534" />
                    ) : (
                      <XCircle size={18} aria-hidden="true" color="#9f1239" />
                    )}
                    <strong style={{ fontSize: '1.1rem', color: markingResult.is_full_marks ? '#166534' : '#9f1239' }}>
                      {markingResult.marks_awarded} / {markingResult.marks_possible} marks awarded
                    </strong>
                  </div>
                  <button
                    onClick={() => setShowMarkScheme(prev => ({ ...prev, [qid]: !prev[qid] }))}
                    style={{
                      background: 'transparent',
                      border: '1px solid #94a3b8',
                      borderRadius: '4px',
                      padding: '0.25rem 0.6rem',
                      fontSize: '0.8rem',
                      cursor: 'pointer',
                      color: '#334155'
                    }}>
                    {showMarkScheme[qid] ? 'Hide Mark Scheme' : 'View Mark Scheme'}
                  </button>
                </div>

                <div style={{ marginBottom: '1rem', padding: '0.75rem', background: '#ffffff', borderRadius: '6px', border: '1px solid #e2e8f0' }}>
                  <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', marginBottom: '0.4rem' }}>
                    Your answer
                  </div>
                  {canvasSnapshots[qid] ? (
                    <img
                      src={canvasSnapshots[qid]}
                      alt="Your handwritten answer"
                      style={{ maxWidth: '100%', border: '1px solid #cbd5e1', borderRadius: '6px' }}
                    />
                  ) : answerType === 'select' || answerType === 'multi_select' ? (
                    <div style={{ color: '#0f172a' }}>
                      {answerText.split(',').map(s => s.trim()).filter(Boolean).map(key => {
                        const opt = ((answerOptions as AnswerOption[]) || []).find(o => o.key === key);
                        return <div key={key}>{opt ? `${opt.key}: ${opt.text}` : key}</div>;
                      })}
                    </div>
                  ) : answerType === 'numeric' ? (
                    <div style={{ color: '#0f172a' }}>
                      {answerText} {(answerOptions as { unit: string } | undefined)?.unit ? formatUnits((answerOptions as { unit: string }).unit) : ''}
                    </div>
                  ) : answerType === 'grid_select' ? (
                    <div style={{ color: '#0f172a' }}>
                      {((answerOptions as GridRow[]) || []).map((row, i) => {
                        const selections = getGridSelections(qid, (answerOptions as GridRow[]) || []);
                        return <div key={i}>{row.statement}: <strong>{selections[i] || '—'}</strong></div>;
                      })}
                    </div>
                  ) : (
                    <div style={{ whiteSpace: 'pre-wrap', color: '#0f172a', fontFamily: "'Crimson Pro', 'Georgia', serif", fontSize: '1.05rem' }}>
                      {answerText}
                    </div>
                  )}
                </div>

                <div style={{ color: '#334155', lineHeight: '1.5', fontSize: '0.95rem' }}>
                  <Markdown>{markingResult.feedback_text}</Markdown>
                </div>

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
                        Misconception detected: {tag}
                      </span>
                    ))}
                  </div>
                )}

                {showMarkScheme[qid] && (
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
                      {question.mark_scheme_text ? (
                        <Markdown>{question.mark_scheme_text}</Markdown>
                      ) : (
                        'Full marks are awarded for identifying required key terms and scientific reasoning aligned with specification code.'
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Per-subquestion Submit */}
            {!markingResult && (() => {
              const incomplete = answerType === 'grid_select'
                ? getGridSelections(qid, (answerOptions as GridRow[]) || []).some(v => !v)
                : (!answerText.trim() && (answerType !== 'written' || mode === 'typed'));
              const isDisabled = isSubmitting || incomplete;
              return (
                <div style={{ fontFamily: 'sans-serif' }}>
                  {submitErrors[qid] && (
                    <div className="banner banner-danger" role="alert" style={{ marginBottom: '0.75rem', justifyContent: 'flex-end' }}>
                      <AlertCircle size={16} />
                      <span>{submitErrors[qid]}</span>
                    </div>
                  )}
                  <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <button
                      onClick={() => handleSubmit(question)}
                      disabled={isDisabled}
                      style={{
                        background: '#dc2626',
                        color: '#ffffff',
                        border: 'none',
                        borderRadius: '6px',
                        padding: '0.65rem 1.5rem',
                        fontWeight: 600,
                        fontSize: '0.95rem',
                        cursor: isDisabled ? 'not-allowed' : 'pointer',
                        opacity: isDisabled ? 0.6 : 1
                      }}>
                      {isSubmitting ? 'Marking…' : `Submit & mark ${question.question_number}`}
                    </button>
                  </div>
                </div>
              );
            })()}
          </div>
        );
      })}

      {/* Action Footer */}
      {allAnswered && onNext && (
        <div style={{ marginTop: '1.5rem', display: 'flex', justifyContent: 'flex-end', fontFamily: 'sans-serif' }}>
          <button
            onClick={onNext}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem',
              background: '#0f172a',
              color: '#ffffff',
              border: 'none',
              borderRadius: '8px',
              padding: '0.65rem 1.5rem',
              fontWeight: 600,
              fontSize: '0.95rem',
              cursor: 'pointer'
            }}>
            {nextLabel}
            <ArrowRight size={16} aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  );
}
