import React, { useState } from 'react';
import api from './api';

export default function AdminIngestion() {
  const [qpFile, setQpFile] = useState<File | null>(null);
  const [msFile, setMsFile] = useState<File | null>(null);
  const [erFile, setErFile] = useState<File | null>(null);
  const [examBoard, setExamBoard] = useState('AQA');
  const [subject, setSubject] = useState('Biology');
  const [paperCode, setPaperCode] = useState('8461/1H');
  const [series, setSeries] = useState('June 2023 Paper 1 Higher');
  const [specCode, setSpecCode] = useState('4.2.1');

  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!qpFile) return;

    setUploading(true);
    setError('');
    setResult(null);

    const formData = new FormData();
    formData.append('file', qpFile);
    if (msFile) formData.append('mark_scheme_file', msFile);
    if (erFile) formData.append('examiner_report_file', erFile);
    formData.append('exam_board', examBoard);
    formData.append('subject', subject);
    formData.append('paper_code', paperCode);
    formData.append('series', series);
    formData.append('spec_code', specCode);

    try {
      const res = await api.post('/admin/ingestion/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      });
      setResult(res.data);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem' }}>
      <div className="card" style={{ maxWidth: '750px', margin: '0 auto' }}>
        <h1 style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>AI Multi-Modal Ingestion Pipeline</h1>
        <p style={{ color: '#94a3b8', marginBottom: '2rem' }}>
          Upload official exam PDFs. The automated pipeline performs PyMuPDF dual visual extraction (raster + vector fallback), dual independent AI image descriptions, pdfplumber table arrays, deterministic DSL compilation, examiner report misconception scanning, and synthetic training dataset generation.
        </p>

        {error && (
          <div style={{ color: '#ef4444', marginBottom: '1.5rem', padding: '1rem', background: '#451a1a', borderRadius: '8px' }}>
            {error}
          </div>
        )}

        <form onSubmit={handleUpload}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1.25rem' }}>
            <div className="form-group">
              <label>Exam Board</label>
              <select value={examBoard} onChange={e => setExamBoard(e.target.value)} className="form-control">
                <option value="AQA">AQA</option>
                <option value="Edexcel">Edexcel (Pearson)</option>
                <option value="OCR">OCR</option>
                <option value="WJEC">WJEC / Eduqas</option>
              </select>
            </div>
            <div className="form-group">
              <label>Subject</label>
              <select value={subject} onChange={e => setSubject(e.target.value)} className="form-control">
                <option value="Biology">Biology (GCSE / A-Level)</option>
                <option value="Chemistry">Chemistry (GCSE / A-Level)</option>
                <option value="Physics">Physics (GCSE / A-Level)</option>
                <option value="Combined Science">Combined Science Trilogy</option>
              </select>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1rem', marginBottom: '1.5rem' }}>
            <div className="form-group">
              <label>Paper Code</label>
              <input type="text" value={paperCode} onChange={e => setPaperCode(e.target.value)} className="form-control" placeholder="8461/1H" required />
            </div>
            <div className="form-group">
              <label>Series / Tier</label>
              <input type="text" value={series} onChange={e => setSeries(e.target.value)} className="form-control" placeholder="June 2023 Higher" required />
            </div>
            <div className="form-group">
              <label>Specification Code</label>
              <input type="text" value={specCode} onChange={e => setSpecCode(e.target.value)} className="form-control" placeholder="4.2.1" required />
            </div>
          </div>

          {/* PDF File Inputs */}
          <div className="form-group" style={{ marginBottom: '1rem' }}>
            <label><strong>1. Question Paper PDF (Required)</strong></label>
            <input type="file" accept=".pdf" onChange={e => setQpFile(e.target.files ? e.target.files[0] : null)} className="form-control" style={{ padding: '0.5rem' }} required />
            <small style={{ color: '#64748b' }}>Contains question stems, mark indicators, and diagrams.</small>
          </div>

          <div className="form-group" style={{ marginBottom: '1rem' }}>
            <label><strong>2. Mark Scheme PDF (Optional)</strong></label>
            <input type="file" accept=".pdf" onChange={e => setMsFile(e.target.files ? e.target.files[0] : null)} className="form-control" style={{ padding: '0.5rem' }} />
            <small style={{ color: '#64748b' }}>Used for deterministic DSL compilation & AI rubrics.</small>
          </div>

          <div className="form-group" style={{ marginBottom: '1.75rem' }}>
            <label><strong>3. Examiner Report PDF (Optional)</strong></label>
            <input type="file" accept=".pdf" onChange={e => setErFile(e.target.files ? e.target.files[0] : null)} className="form-control" style={{ padding: '0.5rem' }} />
            <small style={{ color: '#64748b' }}>Automatically scanned for candidate misconceptions (§6.2a).</small>
          </div>

          <button
            type="submit"
            className="btn btn-primary"
            disabled={!qpFile || uploading}
            style={{ width: '100%', padding: '0.85rem', fontSize: '1.05rem', fontWeight: 700 }}>
            {uploading ? '⚡ Executing AI Multi-Stage Pipeline...' : '🚀 Execute AI Ingestion Pipeline'}
          </button>
        </form>

        {result && (
          <div style={{ marginTop: '2rem', padding: '1.5rem', background: '#1e293b', borderRadius: '10px', border: '1px solid #3b82f6' }}>
            <h3 style={{ marginBottom: '1rem', color: '#38bdf8', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span>✨</span> {result.message}
            </h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', color: '#cbd5e1', fontSize: '0.9rem' }}>
              <div><strong>Paper ID:</strong> <code>{result.paper_id}</code></div>
              <div><strong>Filename:</strong> {result.filename}</div>
              <div><strong>Visuals Extracted:</strong> {result.extracted_visuals_count} (PyMuPDF Dual)</div>
              <div><strong>Tables Extracted:</strong> {result.extracted_tables_count} (pdfplumber)</div>
              <div><strong>Questions Formatted:</strong> {result.questions_extracted}</div>
              <div><strong>Proposed Misconceptions:</strong> {result.proposed_misconceptions_count} (§6.2a)</div>
              <div><strong>Synthetic Training Data:</strong> {result.synthetic_training_examples_count} (§6.2)</div>
            </div>
            <div style={{ marginTop: '1.25rem' }}>
              <a href="/admin/review" className="btn btn-primary" style={{ padding: '0.5rem 1rem', fontSize: '0.9rem' }}>
                Open Ingestion Review Console →
              </a>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
