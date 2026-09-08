import { useState, useEffect } from 'react';
import api from './api';
import EmptyState from './EmptyState';
import Markdown from './Markdown';
import { FileStack, X } from 'lucide-react';

function parseImages(images: any): { url: string; caption?: string }[] {
  if (Array.isArray(images)) return images;
  if (typeof images === 'string') {
    try { return JSON.parse(images); } catch { return []; }
  }
  return [];
}

interface TokenUsage {
  prompt_tokens?: number;
  output_tokens?: number;
  thoughts_tokens?: number;
  total_tokens?: number;
  call_count?: number;
}

// ingestion_token_usage comes back from asyncpg as raw JSONB text (no
// decoded-object codec on the pool), same as images/answer_options
// elsewhere - re-parse it here rather than assuming it's already an object.
function parseTokenUsage(raw: any): TokenUsage | null {
  if (raw && typeof raw === 'object') return raw;
  if (typeof raw === 'string') {
    try { return JSON.parse(raw); } catch { return null; }
  }
  return null;
}

interface QuestionEditForm {
  question_number: string;
  mark_value: number;
  question_text: string;
  marking_type: string;
  marking_dsl: string;
  mark_scheme_text: string;
  needs_review: boolean;
  images: { url: string; caption?: string; [key: string]: any }[];
}

interface Qualification {
  exam_board: string;
  level: string;
  subject: string;
}

interface Props {
  qualification: Qualification;
}

// Papers-review console, scoped to one subject/board/level - the papers
// list, ingestion cost, publish/delete, and per-question calibration that
// used to live in the standalone admin console, now reached from inside
// the subject it belongs to. GET /admin/ingestion/papers still returns
// every paper across every subject; this filters client-side rather than
// needing a dedicated query-param endpoint, since the admin data set here
// is small.
export default function SubjectPapersReview({ qualification }: Props) {
  const [papers, setPapers] = useState<any[]>([]);
  const [selectedPaper, setSelectedPaper] = useState<any | null>(null);
  const [paperDetails, setPaperDetails] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<{ type: 'success' | 'danger'; text: string } | null>(null);

  const [editingQid, setEditingQid] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<QuestionEditForm | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qualification.exam_board, qualification.level, qualification.subject]);

  const fetchData = async () => {
    setLoading(true);
    try {
      const res = await api.get('/admin/ingestion/papers');
      const filtered = res.data.filter((p: any) =>
        p.exam_board === qualification.exam_board &&
        p.level === qualification.level &&
        p.subject === qualification.subject
      );
      setPapers(filtered);
    } catch (err) {
      console.error('Failed to load papers', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSelectPaper = async (paper: any) => {
    setSelectedPaper(paper);
    if (paper.id !== selectedPaper?.id) {
      setEditingQid(null);
      setEditForm(null);
    }
    try {
      const res = await api.get(`/admin/ingestion/paper/${paper.id}`);
      setPaperDetails(res.data);
    } catch (err) {
      console.error('Failed to load paper details', err);
    }
  };

  const handlePublishPaper = async (paperId: string) => {
    try {
      await api.post(`/admin/ingestion/paper/${paperId}/publish`);
      setNotice({ type: 'success', text: 'Paper published to the live student bank.' });
      fetchData();
      if (selectedPaper?.id === paperId) {
        handleSelectPaper(selectedPaper);
      }
    } catch (err) {
      setNotice({ type: 'danger', text: 'Failed to publish paper.' });
    }
  };

  const handleDeletePaper = async (paper: any) => {
    if (!window.confirm(`Delete ${paper.paper_code || 'this paper'} and all its extracted questions? This can't be undone.`)) return;
    try {
      await api.delete(`/admin/ingestion/paper/${paper.id}`);
      setNotice({ type: 'success', text: 'Paper deleted.' });
      if (selectedPaper?.id === paper.id) {
        setSelectedPaper(null);
        setPaperDetails(null);
      }
      fetchData();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setNotice({ type: 'danger', text: detail || 'Failed to delete paper.' });
    }
  };

  const startEdit = (q: any) => {
    setEditingQid(q.id);
    setEditForm({
      question_number: q.question_number || '',
      mark_value: q.mark_value || 0,
      question_text: q.question_text || '',
      marking_type: q.marking_type || 'dsl',
      marking_dsl: q.marking_dsl || '',
      mark_scheme_text: q.mark_scheme_text || '',
      needs_review: !!q.needs_review,
      images: parseImages(q.images),
    });
  };

  const cancelEdit = () => {
    setEditingQid(null);
    setEditForm(null);
  };

  const saveEdit = async (questionId: string) => {
    if (!editForm) return;
    setSavingEdit(true);
    try {
      await api.put(`/admin/ingestion/question/${questionId}`, {
        question_number: editForm.question_number,
        mark_value: editForm.mark_value,
        question_text: editForm.question_text,
        marking_type: editForm.marking_type,
        marking_dsl: editForm.marking_type === 'dsl' ? editForm.marking_dsl : null,
        mark_scheme_text: editForm.mark_scheme_text,
        needs_review: editForm.needs_review,
        images: editForm.images,
      });
      setNotice({ type: 'success', text: 'Question updated.' });
      cancelEdit();
      if (selectedPaper) await handleSelectPaper(selectedPaper);
    } catch (err) {
      setNotice({ type: 'danger', text: 'Failed to update question.' });
    } finally {
      setSavingEdit(false);
    }
  };

  if (loading) {
    return <div className="card"><p style={{ color: 'var(--text-secondary)', margin: 0 }}>Loading papers…</p></div>;
  }

  return (
    <div>
      {notice && (
        <div className={`banner banner-${notice.type}`} role="alert" style={{ marginBottom: '1.5rem' }}>
          <span>{notice.text}</span>
        </div>
      )}

      <div className={selectedPaper ? 'admin-review-grid' : ''} style={selectedPaper ? undefined : { display: 'grid' }}>
        <div className="card">
          <h3>Ingested papers</h3>
          {papers.length === 0 ? (
            <EmptyState
              icon={<FileStack size={36} strokeWidth={1.5} />}
              title="No papers ingested yet"
              description="Upload a past paper from the Upload tab to see it appear here for review."
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '1rem' }}>
              {papers.map((p: any) => (
                <button
                  key={p.id}
                  onClick={() => handleSelectPaper(p)}
                  aria-pressed={selectedPaper?.id === p.id}
                  style={{
                    textAlign: 'left',
                    padding: '1rem',
                    borderRadius: '8px',
                    cursor: 'pointer',
                    background: selectedPaper?.id === p.id ? 'var(--accent-tint)' : 'var(--bg-tertiary)',
                    border: `1px solid ${selectedPaper?.id === p.id ? 'var(--accent-color)' : 'var(--border)'}`,
                    font: 'inherit',
                    color: 'inherit',
                    width: '100%',
                  }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem' }}>
                    <strong style={{ color: 'var(--text-primary)' }}>{p.paper_code || 'Past Paper'}</strong>
                    <span style={{
                      fontSize: '0.75rem',
                      padding: '0.15rem 0.5rem',
                      borderRadius: '4px',
                      fontWeight: 700,
                      background: p.status === 'published' ? 'var(--success-bg)' : 'var(--warning-bg)',
                      color: p.status === 'published' ? 'var(--success)' : 'var(--warning)'
                    }}>
                      {p.status}
                    </span>
                  </div>
                  <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                    {p.tier ? `${p.tier} · ` : ''}{p.series || 'Series'}
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
                    {p.question_count || 0} questions extracted
                    {(() => {
                      const usage = parseTokenUsage(p.ingestion_token_usage);
                      return usage?.total_tokens ? ` · ${usage.total_tokens.toLocaleString()} tokens` : '';
                    })()}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {selectedPaper && paperDetails && (
          <div className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem', borderBottom: '1px solid var(--border)', paddingBottom: '1rem', flexWrap: 'wrap', gap: '1rem' }}>
              <div>
                <h2>{selectedPaper.paper_code}{selectedPaper.tier ? ` · ${selectedPaper.tier}` : ''}</h2>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Source: {selectedPaper.source_pdf_url}</p>
                {(() => {
                  const usage = parseTokenUsage(selectedPaper.ingestion_token_usage);
                  if (!usage?.total_tokens) return null;
                  return (
                    <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                      Ingestion cost: <strong>{usage.total_tokens.toLocaleString()} tokens</strong>
                      {' '}({(usage.prompt_tokens ?? 0).toLocaleString()} prompt, {(usage.output_tokens ?? 0).toLocaleString()} output
                      {usage.thoughts_tokens ? `, ${usage.thoughts_tokens.toLocaleString()} thinking` : ''}
                      {' '}across {usage.call_count ?? 0} calls)
                    </p>
                  );
                })()}
              </div>
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                {selectedPaper.status !== 'published' && (
                  <button onClick={() => handlePublishPaper(selectedPaper.id)} className="btn btn-primary">
                    Publish to question bank
                  </button>
                )}
                <button onClick={() => handleDeletePaper(selectedPaper)} className="btn btn-outline" style={{ color: 'var(--danger)', borderColor: 'var(--danger)' }}>
                  Delete paper
                </button>
              </div>
            </div>

            <h3>Extracted questions ({paperDetails.questions.length})</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', marginTop: '1rem' }}>
              {paperDetails.questions.map((q: any) => {
                const images = parseImages(q.images);
                const isEditing = editingQid === q.id;

                if (isEditing && editForm) {
                  return (
                    <div key={q.id} style={{
                      padding: '1.25rem',
                      borderRadius: '8px',
                      background: 'var(--bg-tertiary)',
                      border: '2px solid var(--accent-color)'
                    }}>
                      <div className="grid-2" style={{ marginBottom: '0.75rem' }}>
                        <div className="form-group">
                          <label htmlFor={`edit-num-${q.id}`}>Question number</label>
                          <input id={`edit-num-${q.id}`} className="form-control" value={editForm.question_number}
                            onChange={e => setEditForm({ ...editForm, question_number: e.target.value })} />
                        </div>
                        <div className="form-group">
                          <label htmlFor={`edit-marks-${q.id}`}>Marks</label>
                          <input id={`edit-marks-${q.id}`} type="number" min={0} className="form-control" value={editForm.mark_value}
                            onChange={e => setEditForm({ ...editForm, mark_value: parseInt(e.target.value, 10) || 0 })} />
                        </div>
                      </div>

                      <div className="form-group" style={{ marginBottom: '0.75rem' }}>
                        <label htmlFor={`edit-text-${q.id}`}>Question text (Markdown)</label>
                        <textarea id={`edit-text-${q.id}`} className="form-control" rows={4} value={editForm.question_text}
                          onChange={e => setEditForm({ ...editForm, question_text: e.target.value })} />
                      </div>

                      <div className="form-group" style={{ marginBottom: '0.75rem' }}>
                        <label htmlFor={`edit-type-${q.id}`}>Marking type</label>
                        <select id={`edit-type-${q.id}`} className="form-control" value={editForm.marking_type}
                          onChange={e => setEditForm({ ...editForm, marking_type: e.target.value })}>
                          <option value="dsl">Deterministic DSL</option>
                          <option value="ai">AI-marked</option>
                        </select>
                      </div>

                      {editForm.marking_type === 'dsl' && (
                        <div className="form-group" style={{ marginBottom: '0.75rem' }}>
                          <label htmlFor={`edit-dsl-${q.id}`}>Marking DSL</label>
                          <input id={`edit-dsl-${q.id}`} className="form-control" style={{ fontFamily: 'monospace' }} value={editForm.marking_dsl}
                            onChange={e => setEditForm({ ...editForm, marking_dsl: e.target.value })} placeholder="e.g. EXACT:0.233 OR RANGE:0.2325,0.2335" />
                        </div>
                      )}

                      <div className="form-group" style={{ marginBottom: '0.75rem' }}>
                        <label htmlFor={`edit-scheme-${q.id}`}>Mark scheme (Markdown)</label>
                        <textarea id={`edit-scheme-${q.id}`} className="form-control" rows={3} value={editForm.mark_scheme_text}
                          onChange={e => setEditForm({ ...editForm, mark_scheme_text: e.target.value })} />
                      </div>

                      {editForm.images.length > 0 && (
                        <div className="form-group" style={{ marginBottom: '0.75rem' }}>
                          <label>Images</label>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem' }}>
                            {editForm.images.map((img, i) => (
                              <div key={i} style={{ position: 'relative' }}>
                                <img
                                  src={img.url}
                                  alt={img.caption || `Figure ${i + 1}`}
                                  style={{ maxWidth: '160px', maxHeight: '160px', borderRadius: '6px', border: '1px solid var(--border)', display: 'block' }}
                                />
                                <button
                                  type="button"
                                  onClick={() => setEditForm({ ...editForm, images: editForm.images.filter((_, idx) => idx !== i) })}
                                  aria-label={`Remove image ${i + 1}`}
                                  title="Remove image"
                                  style={{
                                    position: 'absolute', top: '-8px', right: '-8px',
                                    width: '24px', height: '24px', borderRadius: '50%',
                                    background: 'var(--danger)', color: '#fff', border: 'none',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
                                  }}
                                >
                                  <X size={14} aria-hidden="true" />
                                </button>
                                {img.caption && (
                                  <div style={{ marginTop: '0.25rem', fontSize: '0.75rem', color: 'var(--text-secondary)', maxWidth: '160px' }}>
                                    {img.caption}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem', fontSize: '0.9rem' }}>
                        <input type="checkbox" checked={editForm.needs_review}
                          onChange={e => setEditForm({ ...editForm, needs_review: e.target.checked })} />
                        Still needs review
                      </label>

                      <div style={{ display: 'flex', gap: '0.5rem' }}>
                        <button className="btn btn-primary" disabled={savingEdit} onClick={() => saveEdit(q.id)}>
                          {savingEdit ? 'Saving…' : 'Save'}
                        </button>
                        <button className="btn btn-outline" disabled={savingEdit} onClick={cancelEdit}>Cancel</button>
                      </div>
                    </div>
                  );
                }

                return (
                  <div key={q.id} style={{
                    padding: '1.25rem',
                    borderRadius: '8px',
                    background: 'var(--bg-tertiary)',
                    border: q.needs_review ? '1px solid var(--warning)' : '1px solid var(--border)'
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem', flexWrap: 'wrap', gap: '0.5rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '0.4rem' }}>
                        <strong style={{ fontSize: '1.1rem', color: 'var(--accent-color)', marginRight: '0.25rem' }}>
                          Q{q.question_number}
                        </strong>
                        <span style={{
                          background: q.marking_type === 'dsl' ? 'var(--accent-hover)' : 'var(--warning)',
                          color: '#ffffff',
                          fontSize: '0.75rem',
                          padding: '0.15rem 0.4rem',
                          borderRadius: '4px',
                          fontWeight: 600
                        }}>
                          {q.marking_type === 'dsl' ? 'Deterministic DSL' : 'AI-marked'}
                        </span>
                        {q.needs_review && (
                          <span style={{ background: 'var(--warning-bg)', color: 'var(--warning)', fontSize: '0.75rem', padding: '0.15rem 0.4rem', borderRadius: '4px', fontWeight: 600 }}>
                            Needs review
                          </span>
                        )}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                        <span style={{ fontWeight: 700 }}>[{q.mark_value} marks]</span>
                        <button className="btn btn-outline" style={{ padding: '0.2rem 0.6rem', fontSize: '0.8rem' }} onClick={() => startEdit(q)}>
                          Edit
                        </button>
                      </div>
                    </div>

                    <div style={{ color: 'var(--text-primary)', marginBottom: '0.75rem', fontSize: '0.95rem' }}>
                      <Markdown>{q.question_text}</Markdown>
                    </div>

                    {images.length > 0 && (
                      <div style={{ marginBottom: '0.75rem', textAlign: 'center', background: 'var(--bg-primary)', padding: '0.75rem', borderRadius: '6px', border: '1px solid var(--border)' }}>
                        {images.map((img, i) => (
                          <div key={i} style={{ display: 'inline-block', margin: '0.5rem' }}>
                            <img
                              src={img.url}
                              alt={img.caption || `Figure for Q${q.question_number}`}
                              style={{ maxWidth: '100%', maxHeight: '300px', borderRadius: '6px', border: '1px solid var(--border)' }}
                            />
                            {img.caption && (
                              <div style={{ marginTop: '0.35rem', fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
                                {img.caption}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {q.marking_dsl && (
                      <div style={{ background: 'var(--bg-primary)', padding: '0.5rem 0.75rem', borderRadius: '6px', fontSize: '0.85rem', color: 'var(--accent-color)', fontFamily: 'monospace', marginBottom: '0.5rem' }}>
                        <strong>DSL:</strong> {q.marking_dsl}
                      </div>
                    )}

                    {q.mark_scheme_text && (
                      <div style={{ background: 'var(--bg-tertiary)', padding: '0.5rem 0.75rem', borderRadius: '6px', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                        <strong>Mark scheme:</strong>
                        <Markdown>{q.mark_scheme_text}</Markdown>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
