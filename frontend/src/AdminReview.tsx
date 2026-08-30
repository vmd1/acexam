import { useState, useEffect } from 'react';
import api from './api';
import Spinner from './Spinner';
import EmptyState from './EmptyState';
import Markdown from './Markdown';
import { FileStack, CheckCircle2, AlertCircle, Tag } from 'lucide-react';

function parseImages(images: any): { url: string; caption?: string }[] {
  if (Array.isArray(images)) return images;
  if (typeof images === 'string') {
    try { return JSON.parse(images); } catch { return []; }
  }
  return [];
}

interface QuestionEditForm {
  question_number: string;
  mark_value: number;
  question_text: string;
  marking_type: string;
  marking_dsl: string;
  mark_scheme_text: string;
  needs_review: boolean;
}

export default function AdminReview() {
  const [papers, setPapers] = useState<any[]>([]);
  const [selectedPaper, setSelectedPaper] = useState<any | null>(null);
  const [paperDetails, setPaperDetails] = useState<any | null>(null);
  const [misconceptions, setMisconceptions] = useState<any[]>([]);
  const [activeTab, setActiveTab] = useState<'papers' | 'misconceptions'>('papers');
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<{ type: 'success' | 'danger'; text: string } | null>(null);

  const [editingQid, setEditingQid] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<QuestionEditForm | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [papersRes, miscRes] = await Promise.all([
        api.get('/admin/ingestion/papers'),
        api.get('/admin/ingestion/misconceptions')
      ]);
      setPapers(papersRes.data);
      setMisconceptions(miscRes.data);
    } catch (err) {
      console.error('Failed to load admin data', err);
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

  const handleApproveMisconception = async (spec_code: string, tag_id: string) => {
    try {
      await api.post('/admin/ingestion/misconception/approve', { spec_code, tag_id });
      setNotice({ type: 'success', text: 'Misconception tag approved.' });
      fetchData();
    } catch (err) {
      setNotice({ type: 'danger', text: 'Failed to approve misconception.' });
    }
  };

  if (loading) {
    return <Spinner label="Loading the admin console…" />;
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h1>Admin console</h1>
          <p style={{ color: 'var(--text-secondary)' }}>Review extracted questions, calibrate marking rules, and publish past papers.</p>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }} role="tablist" aria-label="Admin sections">
          <button
            role="tab"
            aria-selected={activeTab === 'papers'}
            onClick={() => setActiveTab('papers')}
            className={`btn ${activeTab === 'papers' ? 'btn-primary' : 'btn-outline'}`}>
            Past papers ({papers.length})
          </button>
          <button
            role="tab"
            aria-selected={activeTab === 'misconceptions'}
            onClick={() => setActiveTab('misconceptions')}
            className={`btn ${activeTab === 'misconceptions' ? 'btn-primary' : 'btn-outline'}`}>
            Misconception taxonomy ({misconceptions.length})
          </button>
        </div>
      </div>

      {notice && (
        <div className={`banner banner-${notice.type}`} role="alert" style={{ marginBottom: '1.5rem' }}>
          {notice.type === 'success' ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
          <span>{notice.text}</span>
        </div>
      )}

      {activeTab === 'papers' && (
        <div className={selectedPaper ? 'admin-review-grid' : ''} style={selectedPaper ? undefined : { display: 'grid' }}>
          {/* Papers List */}
          <div className="card">
            <h3>Ingested papers</h3>
            {papers.length === 0 ? (
              <EmptyState
                icon={<FileStack size={36} strokeWidth={1.5} />}
                title="No papers ingested yet"
                description="Upload a past paper from the ingestion page to see it appear here for review."
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
                      {p.exam_board} {p.subject} • {p.series || 'Series'}
                    </div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
                      {p.question_count || 0} questions extracted
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Selected Paper Details & Question Calibration */}
          {selectedPaper && paperDetails && (
            <div className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem', borderBottom: '1px solid var(--border)', paddingBottom: '1rem', flexWrap: 'wrap', gap: '1rem' }}>
                <div>
                  <h2>{selectedPaper.exam_board} {selectedPaper.subject} - {selectedPaper.paper_code}</h2>
                  <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Source: {selectedPaper.source_pdf_url}</p>
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
      )}

      {activeTab === 'misconceptions' && (
        <div className="card">
          <h3>Canonical misconception taxonomy</h3>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
            Pre-approved taxonomy of misconception tags per spec code, keeping tags stable for student memory tracking.
          </p>

          {misconceptions.length === 0 ? (
            <EmptyState
              icon={<Tag size={36} strokeWidth={1.5} />}
              title="No misconceptions proposed yet"
              description="Ingest a paper with an examiner report to see candidate misconception tags appear here."
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {misconceptions.map((m: any) => (
                <div key={m.id} style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '1rem',
                  borderRadius: '8px',
                  background: 'var(--bg-tertiary)',
                  border: '1px solid var(--border)',
                  flexWrap: 'wrap',
                  gap: '0.75rem',
                }}>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem', flexWrap: 'wrap' }}>
                      <span style={{ background: 'var(--accent-tint)', color: 'var(--accent-color)', padding: '0.15rem 0.4rem', borderRadius: '4px', fontSize: '0.75rem', fontWeight: 700 }}>
                        Spec {m.spec_code}
                      </span>
                      <strong style={{ color: 'var(--text-primary)' }}>{m.label}</strong>
                      <code style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>({m.tag_id})</code>
                    </div>
                    <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{m.description}</div>
                  </div>

                  <div>
                    {m.approved_at ? (
                      <span style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', color: 'var(--success)', fontWeight: 700, fontSize: '0.85rem' }}>
                        <CheckCircle2 size={15} aria-hidden="true" />
                        Approved
                      </span>
                    ) : (
                      <button
                        onClick={() => handleApproveMisconception(m.spec_code, m.tag_id)}
                        className="btn btn-primary"
                        style={{ padding: '0.35rem 0.75rem', fontSize: '0.85rem' }}>
                        Approve tag
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
