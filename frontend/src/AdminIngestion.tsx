import React, { useEffect, useMemo, useState } from 'react';
import api, { extractErrorMessage } from './api';
import { AlertCircle, FileText, CheckCircle2, ArrowRight } from 'lucide-react';

interface Qualification {
  exam_board: string;
  level: string;
  subject: string;
  tiers: string[];
}

export default function AdminIngestion() {
  const [qpFile, setQpFile] = useState<File | null>(null);
  const [msFile, setMsFile] = useState<File | null>(null);
  const [erFile, setErFile] = useState<File | null>(null);

  const [qualifications, setQualifications] = useState<Qualification[]>([]);
  const [selectedQualKey, setSelectedQualKey] = useState('');
  const [tier, setTier] = useState('');

  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');

  const [specFile, setSpecFile] = useState<File | null>(null);
  const [specUploading, setSpecUploading] = useState(false);
  const [specResult, setSpecResult] = useState<any>(null);
  const [specError, setSpecError] = useState('');

  const loadQualifications = async () => {
    try {
      const res = await api.get('/exams/qualifications');
      setQualifications(res.data);
    } catch (err) {
      console.error('Failed to load qualifications', err);
    }
  };

  useEffect(() => { loadQualifications(); }, []);

  const qualKey = (q: Qualification) => `${q.exam_board}::${q.level}::${q.subject}`;
  const selectedQual = useMemo(
    () => qualifications.find(q => qualKey(q) === selectedQualKey) || null,
    [qualifications, selectedQualKey]
  );

  useEffect(() => {
    // Reset tier whenever the qualification changes, since tiers differ per qualification.
    setTier('');
  }, [selectedQualKey]);

  const handleSpecUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!specFile) return;

    setSpecUploading(true);
    setSpecError('');
    setSpecResult(null);

    const formData = new FormData();
    formData.append('file', specFile);

    try {
      const res = await api.post('/admin/ingestion/upload-spec', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      setSpecResult(res.data);
      await loadQualifications();
    } catch (err: any) {
      setSpecError(extractErrorMessage(err, 'Specification upload failed'));
    } finally {
      setSpecUploading(false);
    }
  };

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!qpFile || !selectedQual) return;

    setUploading(true);
    setError('');
    setResult(null);

    const formData = new FormData();
    formData.append('file', qpFile);
    if (msFile) formData.append('mark_scheme_file', msFile);
    if (erFile) formData.append('examiner_report_file', erFile);
    formData.append('exam_board', selectedQual.exam_board);
    formData.append('subject', selectedQual.subject);
    formData.append('level', selectedQual.level);
    if (tier) formData.append('tier', tier);

    try {
      const res = await api.post('/admin/ingestion/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      });
      setResult(res.data);
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Upload failed'));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem' }}>
      <div className="card" style={{ maxWidth: '750px', margin: '0 auto 2rem auto' }}>
        <h2>Pre-populate specification topics</h2>
        <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
          Upload the official exam board specification document to auto-extract the full topic hierarchy
          (e.g. "4.1 Cell biology" → "4.1.1 Cell structure") before ingesting any past papers. Exam board,
          subject and level are detected automatically from the document itself.
        </p>

        {specError && (
          <div className="banner banner-danger" role="alert" style={{ marginBottom: '1.5rem' }}>
            <AlertCircle size={16} />
            <span>{specError}</span>
          </div>
        )}

        <form onSubmit={handleSpecUpload}>
          <div className="form-group" style={{ marginBottom: '1rem' }}>
            <label htmlFor="spec-file"><strong>Specification PDF</strong></label>
            <input id="spec-file" type="file" accept=".pdf" onChange={e => setSpecFile(e.target.files ? e.target.files[0] : null)} className="form-control" style={{ padding: '0.5rem' }} required />
          </div>

          <button
            type="submit"
            className="btn btn-primary"
            disabled={!specFile || specUploading}
            style={{ width: '100%', padding: '0.65rem' }}>
            {specUploading ? 'Extracting topics…' : 'Extract topics from specification'}
          </button>
        </form>

        {specResult && (
          <div className="banner banner-success" role="status" style={{ marginTop: '1.5rem' }}>
            <CheckCircle2 size={16} />
            <span>
              Detected <strong>{specResult.exam_board} {specResult.level} {specResult.subject}</strong> — {specResult.message} <strong>{specResult.topics_created}</strong> topics created/updated.
            </span>
          </div>
        )}
      </div>

      <div className="card" style={{ maxWidth: '750px', margin: '0 auto' }}>
        <h1 style={{ fontSize: 'var(--text-h2)' }}>AI ingestion pipeline</h1>
        <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
          Upload official exam PDFs. The pipeline extracts diagrams and tables, splits and classifies each question,
          compiles deterministic marking rules where it can, and scans the mark scheme and examiner report for
          candidate misconceptions. Paper code and series are read straight off the PDF itself.
        </p>

        {error && (
          <div className="banner banner-danger" role="alert" style={{ marginBottom: '1.5rem' }}>
            <AlertCircle size={16} />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleUpload}>
          {qualifications.length === 0 ? (
            <div className="banner banner-danger" role="alert" style={{ marginBottom: '1.5rem' }}>
              <AlertCircle size={16} />
              <span>No specifications ingested yet. Upload a specification above first, so questions can be classified against real topics.</span>
            </div>
          ) : (
            <div className="grid-2" style={{ marginBottom: '1.5rem' }}>
              <div className="form-group">
                <label htmlFor="qp-qual">Qualification</label>
                <select id="qp-qual" value={selectedQualKey} onChange={e => setSelectedQualKey(e.target.value)} className="form-control" required>
                  <option value="" disabled>Select…</option>
                  {qualifications.map(q => (
                    <option key={qualKey(q)} value={qualKey(q)}>{q.exam_board} {q.level} {q.subject}</option>
                  ))}
                </select>
              </div>
              {selectedQual && selectedQual.tiers.length > 0 && (
                <div className="form-group">
                  <label htmlFor="qp-tier">Tier</label>
                  <select id="qp-tier" value={tier} onChange={e => setTier(e.target.value)} className="form-control" required>
                    <option value="" disabled>Select…</option>
                    {selectedQual.tiers.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
              )}
            </div>
          )}

          {/* PDF File Inputs */}
          <div className="form-group" style={{ marginBottom: '1rem' }}>
            <label htmlFor="qp-file"><strong>1. Question paper PDF (required)</strong></label>
            <input id="qp-file" type="file" accept=".pdf" onChange={e => setQpFile(e.target.files ? e.target.files[0] : null)} className="form-control" style={{ padding: '0.5rem' }} required />
            <small style={{ color: 'var(--text-muted)' }}>Contains question stems, mark indicators, and diagrams.</small>
          </div>

          <div className="form-group" style={{ marginBottom: '1rem' }}>
            <label htmlFor="ms-file"><strong>2. Mark scheme PDF (optional)</strong></label>
            <input id="ms-file" type="file" accept=".pdf" onChange={e => setMsFile(e.target.files ? e.target.files[0] : null)} className="form-control" style={{ padding: '0.5rem' }} />
            <small style={{ color: 'var(--text-muted)' }}>Used for deterministic DSL compilation, AI rubrics, and scanning for candidate misconceptions.</small>
          </div>

          <div className="form-group" style={{ marginBottom: '1.75rem' }}>
            <label htmlFor="er-file"><strong>3. Examiner report PDF (optional)</strong></label>
            <input id="er-file" type="file" accept=".pdf" onChange={e => setErFile(e.target.files ? e.target.files[0] : null)} className="form-control" style={{ padding: '0.5rem' }} />
            <small style={{ color: 'var(--text-muted)' }}>Automatically scanned for candidate misconceptions.</small>
          </div>

          <button
            type="submit"
            className="btn btn-primary"
            disabled={!qpFile || !selectedQual || (selectedQual.tiers.length > 0 && !tier) || uploading}
            style={{ width: '100%', padding: '0.85rem', fontSize: '1.05rem', fontWeight: 700 }}>
            {uploading ? 'Running the ingestion pipeline…' : 'Run ingestion pipeline'}
          </button>
        </form>

        {result && (
          <div style={{ marginTop: '2rem', padding: '1.5rem', background: 'var(--bg-tertiary)', borderRadius: '10px', border: '1px solid var(--border)' }}>
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <FileText size={18} aria-hidden="true" />
              {result.message}
            </h3>
            <div className="grid-2" style={{ marginTop: '0.75rem', color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
              <div><strong>Paper ID:</strong> <code>{result.paper_id}</code></div>
              <div><strong>Filename:</strong> {result.filename}</div>
              <div><strong>Visuals extracted:</strong> {result.extracted_visuals_count}</div>
              <div><strong>Tables extracted:</strong> {result.extracted_tables_count}</div>
              <div><strong>Questions formatted:</strong> {result.questions_extracted}</div>
              <div><strong>Proposed misconceptions:</strong> {result.proposed_misconceptions_count}</div>
              <div><strong>Synthetic training data:</strong> {result.synthetic_training_examples_count}</div>
            </div>
            <div style={{ marginTop: '1.25rem' }}>
              <a href="/admin/review" className="btn btn-primary" style={{ padding: '0.5rem 1rem', fontSize: '0.9rem' }}>
                Open review console
                <ArrowRight size={15} aria-hidden="true" />
              </a>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
