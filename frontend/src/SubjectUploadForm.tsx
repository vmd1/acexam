import React, { useState } from 'react';
import api, { extractErrorMessage } from './api';
import { AlertCircle, FileText, Plus, Trash2, CheckCircle2, XCircle, Loader2 } from 'lucide-react';

interface Qualification {
  id: string;
  exam_board: string;
  level: string;
  subject: string;
  tiers: string[];
}

interface Props {
  qualification: Qualification;
  onUploaded?: () => void;
}

type RowStatus = 'pending' | 'uploading' | 'success' | 'error';

interface BulkRow {
  id: number;
  qpFile: File | null;
  msFile: File | null;
  erFile: File | null;
  tier: string;
  status: RowStatus;
  result?: any;
  error?: string;
}

let nextRowId = 1;
const makeRow = (): BulkRow => ({ id: nextRowId++, qpFile: null, msFile: null, erFile: null, tier: '', status: 'pending' });

async function uploadOne(qualification: Qualification, row: BulkRow) {
  const formData = new FormData();
  formData.append('file', row.qpFile as File);
  if (row.msFile) formData.append('mark_scheme_file', row.msFile);
  if (row.erFile) formData.append('examiner_report_file', row.erFile);
  formData.append('exam_board', qualification.exam_board);
  formData.append('subject', qualification.subject);
  formData.append('level', qualification.level);
  if (row.tier) formData.append('tier', row.tier);
  const res = await api.post('/admin/ingestion/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  });
  return res.data;
}

// Ingestion upload form, scoped to one already-known qualification (no
// qualification picker - the subject the admin is already inside is the
// only option). Tier still needs picking per upload since one qualification
// can cover multiple tiers (Higher/Foundation) sharing the same spec.
//
// Two modes: a single-paper form (unchanged behaviour), and a bulk mode
// where the admin builds a list of question-paper rows - each with its own
// optional mark scheme/examiner report and tier, mirroring the single form
// per row rather than trying to auto-pair files by filename. Rows are
// uploaded to the existing one-paper-at-a-time /upload endpoint sequentially
// (not in parallel) since ingestion is AI-call-heavy per paper and the
// backend isn't set up for concurrent ingestion jobs.
export default function SubjectUploadForm({ qualification, onUploaded }: Props) {
  const [mode, setMode] = useState<'single' | 'bulk'>('single');

  const [qpFile, setQpFile] = useState<File | null>(null);
  const [msFile, setMsFile] = useState<File | null>(null);
  const [erFile, setErFile] = useState<File | null>(null);
  const [tier, setTier] = useState('');

  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');

  const [rows, setRows] = useState<BulkRow[]>([makeRow()]);
  const [bulkRunning, setBulkRunning] = useState(false);

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!qpFile) return;

    setUploading(true);
    setError('');
    setResult(null);

    try {
      const data = await uploadOne(qualification, { id: 0, qpFile, msFile, erFile, tier, status: 'pending' });
      setResult(data);
      setQpFile(null);
      setMsFile(null);
      setErFile(null);
      onUploaded?.();
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Upload failed'));
    } finally {
      setUploading(false);
    }
  };

  const updateRow = (id: number, patch: Partial<BulkRow>) => {
    setRows(rs => rs.map(r => (r.id === id ? { ...r, ...patch } : r)));
  };

  const addRow = () => setRows(rs => [...rs, makeRow()]);
  const removeRow = (id: number) => setRows(rs => (rs.length > 1 ? rs.filter(r => r.id !== id) : rs));

  const runBulkUpload = async () => {
    setBulkRunning(true);
    // Snapshot which rows are actually eligible to run before starting -
    // rows are locked (inputs disabled) for the duration, so this list
    // can't drift mid-run.
    const toRun = rows.filter(r => r.qpFile && (qualification.tiers.length === 0 || r.tier));
    for (const row of toRun) {
      updateRow(row.id, { status: 'uploading', error: undefined, result: undefined });
      try {
        const data = await uploadOne(qualification, row);
        updateRow(row.id, { status: 'success', result: data });
      } catch (err: any) {
        updateRow(row.id, { status: 'error', error: extractErrorMessage(err, 'Upload failed') });
      }
    }
    setBulkRunning(false);
    onUploaded?.();
  };

  const bulkHasRunnable = rows.some(r => r.qpFile && (qualification.tiers.length === 0 || r.tier));

  return (
    <div className="card" style={{ maxWidth: mode === 'bulk' ? '1000px' : '750px' }}>
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem' }}>
        <button
          type="button"
          className={`btn ${mode === 'single' ? 'btn-primary' : 'btn-outline'}`}
          onClick={() => setMode('single')}
          disabled={uploading || bulkRunning}
        >
          Single paper
        </button>
        <button
          type="button"
          className={`btn ${mode === 'bulk' ? 'btn-primary' : 'btn-outline'}`}
          onClick={() => setMode('bulk')}
          disabled={uploading || bulkRunning}
        >
          Bulk upload
        </button>
      </div>

      {mode === 'single' ? (
        <>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
            Upload official exam PDFs for {qualification.subject} ({qualification.level} {qualification.exam_board}).
            The pipeline extracts diagrams and tables, splits and classifies each question, compiles
            deterministic marking rules where it can, and scans the mark scheme and examiner report for
            candidate misconceptions. Paper code and series are read straight off the PDF itself.
          </p>

          {error && (
            <div className="banner banner-danger" role="alert" style={{ marginBottom: '1.5rem' }}>
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}

          <form onSubmit={handleUpload}>
            {qualification.tiers.length > 0 && (
              <div className="form-group" style={{ marginBottom: '1.5rem', maxWidth: '260px' }}>
                <label htmlFor="qp-tier">Tier</label>
                <select id="qp-tier" value={tier} onChange={e => setTier(e.target.value)} className="form-control" required>
                  <option value="" disabled>Select…</option>
                  {qualification.tiers.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
            )}

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
              disabled={!qpFile || (qualification.tiers.length > 0 && !tier) || uploading}
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
            </div>
          )}
        </>
      ) : (
        <>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
            Queue up several question papers for {qualification.subject} ({qualification.level} {qualification.exam_board})
            at once. Add a row per paper, each with its own optional mark scheme/examiner report and tier, then run the
            batch - papers are ingested one at a time, and a failure on one row doesn't stop the rest.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginBottom: '1.5rem' }}>
            {rows.map((row, idx) => (
              <div key={row.id} style={{ padding: '1.25rem', background: 'var(--bg-tertiary)', borderRadius: '10px', border: '1px solid var(--border)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                  <strong>Paper {idx + 1}</strong>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    {row.status === 'uploading' && <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: 'var(--text-secondary)' }}><Loader2 size={16} className="spin" aria-hidden="true" /> Uploading…</span>}
                    {row.status === 'success' && <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: 'var(--success)' }}><CheckCircle2 size={16} aria-hidden="true" /> Done</span>}
                    {row.status === 'error' && <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: 'var(--danger)' }}><XCircle size={16} aria-hidden="true" /> Failed</span>}
                    <button
                      type="button"
                      className="btn btn-outline"
                      onClick={() => removeRow(row.id)}
                      disabled={bulkRunning || rows.length === 1}
                      aria-label={`Remove paper ${idx + 1}`}
                      style={{ padding: '0.4rem 0.6rem' }}
                    >
                      <Trash2 size={14} aria-hidden="true" />
                    </button>
                  </div>
                </div>

                <div className="grid-2" style={{ gap: '1rem' }}>
                  {qualification.tiers.length > 0 && (
                    <div className="form-group">
                      <label htmlFor={`tier-${row.id}`}>Tier</label>
                      <select
                        id={`tier-${row.id}`}
                        value={row.tier}
                        onChange={e => updateRow(row.id, { tier: e.target.value })}
                        className="form-control"
                        disabled={bulkRunning}
                        required
                      >
                        <option value="" disabled>Select…</option>
                        {qualification.tiers.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </div>
                  )}
                  <div className="form-group">
                    <label htmlFor={`qp-${row.id}`}>Question paper PDF (required)</label>
                    <input
                      id={`qp-${row.id}`}
                      type="file"
                      accept=".pdf"
                      onChange={e => updateRow(row.id, { qpFile: e.target.files ? e.target.files[0] : null })}
                      className="form-control"
                      style={{ padding: '0.5rem' }}
                      disabled={bulkRunning}
                      required
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor={`ms-${row.id}`}>Mark scheme PDF (optional)</label>
                    <input
                      id={`ms-${row.id}`}
                      type="file"
                      accept=".pdf"
                      onChange={e => updateRow(row.id, { msFile: e.target.files ? e.target.files[0] : null })}
                      className="form-control"
                      style={{ padding: '0.5rem' }}
                      disabled={bulkRunning}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor={`er-${row.id}`}>Examiner report PDF (optional)</label>
                    <input
                      id={`er-${row.id}`}
                      type="file"
                      accept=".pdf"
                      onChange={e => updateRow(row.id, { erFile: e.target.files ? e.target.files[0] : null })}
                      className="form-control"
                      style={{ padding: '0.5rem' }}
                      disabled={bulkRunning}
                    />
                  </div>
                </div>

                {row.status === 'error' && row.error && (
                  <div className="banner banner-danger" role="alert" style={{ marginTop: '1rem' }}>
                    <AlertCircle size={16} />
                    <span>{row.error}</span>
                  </div>
                )}

                {row.status === 'success' && row.result && (
                  <div style={{ marginTop: '1rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                    <code>{row.result.paper_id}</code> · {row.result.questions_extracted} questions · {row.result.proposed_misconceptions_count} proposed misconceptions
                  </div>
                )}
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: '0.75rem' }}>
            <button type="button" className="btn btn-outline" onClick={addRow} disabled={bulkRunning} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <Plus size={15} aria-hidden="true" /> Add paper
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={runBulkUpload}
              disabled={bulkRunning || !bulkHasRunnable}
              style={{ flex: 1, padding: '0.85rem', fontSize: '1.05rem', fontWeight: 700 }}
            >
              {bulkRunning ? 'Running ingestion pipeline…' : `Run ingestion pipeline (${rows.filter(r => r.qpFile && (qualification.tiers.length === 0 || r.tier)).length} of ${rows.length} ready)`}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
