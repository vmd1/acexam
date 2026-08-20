import React, { useState, useEffect } from 'react';
import api from './api';

export default function AdminReview() {
  const [papers, setPapers] = useState<any[]>([]);
  const [selectedPaper, setSelectedPaper] = useState<any | null>(null);
  const [paperDetails, setPaperDetails] = useState<any | null>(null);
  const [misconceptions, setMisconceptions] = useState<any[]>([]);
  const [activeTab, setActiveTab] = useState<'papers' | 'misconceptions'>('papers');
  const [loading, setLoading] = useState(true);

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
      alert('Paper published successfully to the live student bank!');
      fetchData();
      if (selectedPaper?.id === paperId) {
        handleSelectPaper(selectedPaper);
      }
    } catch (err) {
      alert('Failed to publish paper');
    }
  };

  const handleApproveMisconception = async (spec_code: string, tag_id: string) => {
    try {
      await api.post('/admin/ingestion/misconception/approve', { spec_code, tag_id });
      alert('Misconception tag approved!');
      fetchData();
    } catch (err) {
      alert('Failed to approve misconception');
    }
  };

  if (loading) {
    return <div className="container text-center" style={{ marginTop: '4rem' }}><h2>Loading Admin Console...</h2></div>;
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem' }}>
        <div>
          <h1 style={{ fontSize: '2.2rem', marginBottom: '0.25rem' }}>Admin Ingestion Console</h1>
          <p style={{ color: '#94a3b8' }}>Review PyMuPDF extraction, calibrate deterministic DSL rubrics, and publish past papers.</p>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            onClick={() => setActiveTab('papers')}
            className={`btn ${activeTab === 'papers' ? 'btn-primary' : 'btn-outline'}`}>
            📄 Past Papers ({papers.length})
          </button>
          <button
            onClick={() => setActiveTab('misconceptions')}
            className={`btn ${activeTab === 'misconceptions' ? 'btn-primary' : 'btn-outline'}`}>
            🏷️ Misconception Taxonomy ({misconceptions.length})
          </button>
        </div>
      </div>

      {activeTab === 'papers' && (
        <div style={{ display: 'grid', gridTemplateColumns: selectedPaper ? '350px 1fr' : '1fr', gap: '2rem' }}>
          {/* Papers List */}
          <div className="card">
            <h3 style={{ marginBottom: '1rem' }}>Ingested Papers</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {papers.map((p: any) => (
                <div
                  key={p.id}
                  onClick={() => handleSelectPaper(p)}
                  style={{
                    padding: '1rem',
                    borderRadius: '8px',
                    cursor: 'pointer',
                    background: selectedPaper?.id === p.id ? 'rgba(99, 102, 241, 0.15)' : 'rgba(255, 255, 255, 0.03)',
                    border: `1px solid ${selectedPaper?.id === p.id ? '#6366f1' : 'rgba(255, 255, 255, 0.08)'}`
                  }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem' }}>
                    <strong style={{ color: '#ffffff' }}>{p.paper_code || 'Past Paper'}</strong>
                    <span style={{
                      fontSize: '0.75rem',
                      padding: '0.15rem 0.5rem',
                      borderRadius: '4px',
                      fontWeight: 700,
                      background: p.status === 'published' ? 'rgba(16, 185, 129, 0.2)' : 'rgba(245, 158, 11, 0.2)',
                      color: p.status === 'published' ? '#34d399' : '#fbbf24'
                    }}>
                      {p.status}
                    </span>
                  </div>
                  <div style={{ fontSize: '0.85rem', color: '#94a3b8' }}>
                    {p.exam_board} {p.subject} • {p.series || 'Series'}
                  </div>
                  <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.35rem' }}>
                    {p.question_count || 0} questions extracted
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Selected Paper Details & Question Calibration */}
          {selectedPaper && paperDetails && (
            <div className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '1rem' }}>
                <div>
                  <h2>{selectedPaper.exam_board} {selectedPaper.subject} - {selectedPaper.paper_code}</h2>
                  <p style={{ color: '#94a3b8', fontSize: '0.85rem' }}>Source: {selectedPaper.source_pdf_url}</p>
                </div>
                {selectedPaper.status !== 'published' && (
                  <button onClick={() => handlePublishPaper(selectedPaper.id)} className="btn btn-primary">
                    ✅ Publish to Question Bank
                  </button>
                )}
              </div>

              <h3>Extracted Questions ({paperDetails.questions.length})</h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', marginTop: '1rem' }}>
                {paperDetails.questions.map((q: any) => (
                  <div key={q.id} style={{
                    padding: '1.25rem',
                    borderRadius: '8px',
                    background: 'rgba(255, 255, 255, 0.02)',
                    border: '1px solid rgba(255, 255, 255, 0.08)'
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                      <div>
                        <strong style={{ fontSize: '1.1rem', color: '#6366f1', marginRight: '0.5rem' }}>
                          Q{q.question_number}
                        </strong>
                        <span style={{
                          background: q.marking_type === 'dsl' ? '#4338ca' : '#92400e',
                          color: '#ffffff',
                          fontSize: '0.75rem',
                          padding: '0.15rem 0.4rem',
                          borderRadius: '4px',
                          fontWeight: 600
                        }}>
                          {q.marking_type === 'dsl' ? 'Deterministic DSL' : 'AI Spec Model'}
                        </span>
                      </div>
                      <span style={{ fontWeight: 700 }}>[{q.mark_value} marks]</span>
                    </div>

                    <p style={{ color: '#cbd5e1', marginBottom: '0.75rem', fontSize: '0.95rem' }}>
                      {q.question_text}
                    </p>

                    {q.marking_dsl && (
                      <div style={{ background: '#0f172a', padding: '0.5rem 0.75rem', borderRadius: '6px', fontSize: '0.85rem', color: '#a5b4fc', fontFamily: 'monospace', marginBottom: '0.5rem' }}>
                        <strong>DSL:</strong> {q.marking_dsl}
                      </div>
                    )}

                    {q.mark_scheme_text && (
                      <div style={{ background: 'rgba(255,255,255,0.03)', padding: '0.5rem 0.75rem', borderRadius: '6px', fontSize: '0.85rem', color: '#94a3b8' }}>
                        <strong>Mark Scheme:</strong> {q.mark_scheme_text}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'misconceptions' && (
        <div className="card">
          <h3 style={{ marginBottom: '1rem' }}>Canonical Misconception Taxonomy</h3>
          <p style={{ color: '#94a3b8', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
            Pre-approved taxonomy of misconception tags per spec code. Ensures stable tags for student memory resolution.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {misconceptions.map((m: any) => (
              <div key={m.id} style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '1rem',
                borderRadius: '8px',
                background: 'rgba(255, 255, 255, 0.03)',
                border: '1px solid rgba(255, 255, 255, 0.05)'
              }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
                    <span style={{ background: '#312e81', color: '#c7d2fe', padding: '0.15rem 0.4rem', borderRadius: '4px', fontSize: '0.75rem', fontWeight: 700 }}>
                      Spec {m.spec_code}
                    </span>
                    <strong style={{ color: '#ffffff' }}>{m.label}</strong>
                    <code style={{ fontSize: '0.8rem', color: '#94a3b8' }}>({m.tag_id})</code>
                  </div>
                  <div style={{ fontSize: '0.85rem', color: '#94a3b8' }}>{m.description}</div>
                </div>

                <div>
                  {m.approved_at ? (
                    <span style={{ color: '#10b981', fontWeight: 700, fontSize: '0.85rem' }}>
                      ✅ Approved
                    </span>
                  ) : (
                    <button
                      onClick={() => handleApproveMisconception(m.spec_code, m.tag_id)}
                      className="btn btn-primary"
                      style={{ padding: '0.35rem 0.75rem', fontSize: '0.85rem' }}>
                      Approve Tag
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
