import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api, { extractErrorMessage } from './api';
import Spinner from './Spinner';
import EmptyState from './EmptyState';
import Modal from './Modal';
import { AlertCircle, Award, BookOpen, Check, ChevronRight, ClipboardX, FileStack, MoreVertical, Plus, Timer } from 'lucide-react';

interface Qualification {
  id: string;
  exam_board: string;
  level: string;
  subject: string;
  tiers: string[];
  custom_paper_target_marks: number | null;
  custom_paper_time_limit_minutes: number | null;
  paper_count: number;
}

export default function AdminManageSubjects() {
  const [qualifications, setQualifications] = useState<Qualification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [addOpen, setAddOpen] = useState(false);
  const [newBoard, setNewBoard] = useState('');
  const [newLevel, setNewLevel] = useState('');
  const [newSubject, setNewSubject] = useState('');
  const [newSpecFile, setNewSpecFile] = useState<File | null>(null);
  const [addSubmitting, setAddSubmitting] = useState(false);
  const [addError, setAddError] = useState('');

  const [menuOpen, setMenuOpen] = useState(false);
  const [gbOpen, setGbOpen] = useState(false);
  const [gbBoard, setGbBoard] = useState('');
  const [gbLevel, setGbLevel] = useState('');
  const [gbFile, setGbFile] = useState<File | null>(null);
  const [gbSubmitting, setGbSubmitting] = useState(false);
  const [gbError, setGbError] = useState('');
  const [gbResult, setGbResult] = useState<{ updated: { subject: string; tier: string; grades: number }[]; skipped: { subject: string; tier: string; reason: string }[] } | null>(null);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await api.get('/admin/qualifications');
      setQualifications(res.data);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load qualifications'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const resetAddForm = () => {
    setNewBoard('');
    setNewLevel('');
    setNewSubject('');
    setNewSpecFile(null);
    setAddError('');
  };

  const closeAddModal = () => {
    setAddOpen(false);
    resetAddForm();
  };

  const handleAddSubject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newBoard.trim() || !newLevel.trim() || !newSubject.trim() || !newSpecFile) return;

    setAddSubmitting(true);
    setAddError('');

    const formData = new FormData();
    formData.append('file', newSpecFile);
    formData.append('exam_board', newBoard.trim());
    formData.append('level', newLevel.trim());
    formData.append('subject', newSubject.trim());

    try {
      await api.post('/admin/ingestion/upload-spec', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      closeAddModal();
      await load();
    } catch (err) {
      setAddError(extractErrorMessage(err, 'Failed to add subject'));
    } finally {
      setAddSubmitting(false);
    }
  };

  // (board, level) combos that actually have qualifications - what the
  // grade boundaries doc's dropdowns offer, since ingest looks up
  // qualifications by exactly these two fields.
  const boardLevelOptions = Array.from(
    new Map(qualifications.map(q => [`${q.exam_board}|||${q.level}`, { exam_board: q.exam_board, level: q.level }])).values()
  ).sort((a, b) => a.exam_board.localeCompare(b.exam_board) || a.level.localeCompare(b.level));

  const resetGbForm = () => {
    setGbBoard('');
    setGbLevel('');
    setGbFile(null);
    setGbError('');
    setGbResult(null);
  };

  const closeGbModal = () => {
    setGbOpen(false);
    resetGbForm();
  };

  const handleIngestGradeBoundaries = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!gbBoard.trim() || !gbLevel.trim() || !gbFile) return;

    setGbSubmitting(true);
    setGbError('');
    setGbResult(null);

    const formData = new FormData();
    formData.append('file', gbFile);
    formData.append('exam_board', gbBoard.trim());
    formData.append('level', gbLevel.trim());

    try {
      const res = await api.post('/admin/qualifications/grade-boundaries/ingest', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      setGbResult(res.data);
    } catch (err) {
      setGbError(extractErrorMessage(err, 'Failed to update grade boundaries'));
    } finally {
      setGbSubmitting(false);
    }
  };

  if (loading) {
    return <Spinner label="Loading qualifications…" />;
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem', maxWidth: '960px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', marginBottom: '0.5rem' }}>
        <h1 style={{ marginBottom: 0 }}>Manage subjects</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button
            className="btn btn-primary"
            onClick={() => setAddOpen(true)}
            style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', whiteSpace: 'nowrap' }}
          >
            <Plus size={16} aria-hidden="true" />
            Add subject
          </button>
          <div style={{ position: 'relative' }}>
            <button
              className="btn btn-outline"
              onClick={() => setMenuOpen(o => !o)}
              aria-haspopup="true"
              aria-expanded={menuOpen}
              aria-label="More actions"
              style={{ padding: '0.6rem' }}
            >
              <MoreVertical size={18} aria-hidden="true" />
            </button>
            {menuOpen && (
              <>
                <div style={{ position: 'fixed', inset: 0, zIndex: 150 }} onClick={() => setMenuOpen(false)} />
                <div className="card menu-popover" role="menu">
                  <button
                    type="button"
                    role="menuitem"
                    className="menu-item"
                    onClick={() => { setMenuOpen(false); setGbOpen(true); }}
                  >
                    <Award size={15} aria-hidden="true" />
                    Update grade boundaries
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
        Every ingested qualification, one card each. Open a subject to configure its custom-paper
        settings, exam tiers, and AI marking model rollout.
      </p>

      {error && (
        <div className="banner banner-danger" role="alert" style={{ marginBottom: '1.5rem' }}>
          <span>{error}</span>
        </div>
      )}

      {qualifications.length === 0 ? (
        <EmptyState
          icon={<ClipboardX size={40} strokeWidth={1.5} />}
          title="No qualifications yet"
          description="Add a subject above to create your first qualification."
        />
      ) : (
        <div className="subject-grid">
          {qualifications.map(q => {
            const configured = q.custom_paper_target_marks != null && q.custom_paper_time_limit_minutes != null;
            return (
              <Link key={q.id} to={`/admin/subjects/${q.id}`} className="subject-card">
                <div className="subject-card-icon">
                  <BookOpen size={22} aria-hidden="true" />
                </div>
                <div className="subject-card-body">
                  <div className="subject-card-title">{q.subject}</div>
                  <div className="subject-card-meta">{q.level} · {q.exam_board}</div>
                  <div className="subject-card-tiers">
                    {q.tiers.map(t => (
                      <span key={t} className="tier-chip">{t}</span>
                    ))}
                  </div>
                  <div className="subject-card-stats">
                    <span>
                      <FileStack size={14} aria-hidden="true" />
                      {q.paper_count} {q.paper_count === 1 ? 'paper' : 'papers'}
                    </span>
                    <span>
                      <Timer size={14} aria-hidden="true" />
                      {configured
                        ? `${q.custom_paper_target_marks} marks · ${q.custom_paper_time_limit_minutes} min`
                        : 'Not configured'}
                    </span>
                  </div>
                </div>
                <ChevronRight size={20} aria-hidden="true" className="subject-card-chevron" />
              </Link>
            );
          })}
        </div>
      )}

      {addOpen && (
        <Modal title="Add subject" onClose={closeAddModal}>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
            Creates the qualification and extracts its full topic hierarchy (and tiers, e.g.
            Higher/Foundation) straight from the specification document - everything besides these
            three fields is derived from the spec itself.
          </p>

          {addError && (
            <div className="banner banner-danger" role="alert" style={{ marginBottom: '1.5rem' }}>
              <AlertCircle size={16} />
              <span>{addError}</span>
            </div>
          )}

          <form onSubmit={handleAddSubject}>
            <div className="form-group" style={{ marginBottom: '1rem' }}>
              <label htmlFor="new-board">Exam board</label>
              <input
                id="new-board"
                type="text"
                className="form-control"
                placeholder="e.g. AQA"
                value={newBoard}
                onChange={e => setNewBoard(e.target.value)}
                required
              />
            </div>

            <div className="form-group" style={{ marginBottom: '1rem' }}>
              <label htmlFor="new-level">Level</label>
              <input
                id="new-level"
                type="text"
                className="form-control"
                placeholder="e.g. GCSE"
                value={newLevel}
                onChange={e => setNewLevel(e.target.value)}
                required
              />
            </div>

            <div className="form-group" style={{ marginBottom: '1rem' }}>
              <label htmlFor="new-subject">Name</label>
              <input
                id="new-subject"
                type="text"
                className="form-control"
                placeholder="e.g. Physics"
                value={newSubject}
                onChange={e => setNewSubject(e.target.value)}
                required
              />
            </div>

            <div className="form-group" style={{ marginBottom: '1.5rem' }}>
              <label htmlFor="new-spec-file">Specification PDF</label>
              <input
                id="new-spec-file"
                type="file"
                accept=".pdf"
                className="form-control"
                style={{ padding: '0.5rem' }}
                onChange={e => setNewSpecFile(e.target.files ? e.target.files[0] : null)}
                required
              />
            </div>

            <button
              type="submit"
              className="btn btn-primary"
              disabled={!newBoard.trim() || !newLevel.trim() || !newSubject.trim() || !newSpecFile || addSubmitting}
              style={{ width: '100%', padding: '0.65rem' }}
            >
              {addSubmitting ? 'Adding subject…' : 'Add subject'}
            </button>
          </form>
        </Modal>
      )}

      {gbOpen && (
        <Modal title="Update grade boundaries" onClose={closeGbModal}>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
            Upload an exam board's official grade boundaries document for one level (e.g. all OCR GCSE
            boundaries for a series) - it updates every subject and tier the document covers in one go,
            matched against the qualifications already added below.
          </p>

          {gbError && (
            <div className="banner banner-danger" role="alert" style={{ marginBottom: '1.5rem' }}>
              <AlertCircle size={16} />
              <span>{gbError}</span>
            </div>
          )}

          {gbResult && (
            <div className="banner banner-success" role="status" style={{ marginBottom: '1.5rem', flexDirection: 'column', alignItems: 'flex-start', gap: '0.5rem' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                <Check size={16} aria-hidden="true" />
                Updated {gbResult.updated.length} subject/tier{gbResult.updated.length === 1 ? '' : 's'}.
              </span>
              {gbResult.updated.length > 0 && (
                <ul style={{ margin: 0, paddingLeft: '1.2rem', fontSize: '0.85rem' }}>
                  {gbResult.updated.map((u, i) => (
                    <li key={i}>{u.subject}{u.tier ? ` (${u.tier})` : ''} - {u.grades} grades</li>
                  ))}
                </ul>
              )}
              {gbResult.skipped.length > 0 && (
                <>
                  <span style={{ fontSize: '0.85rem' }}>Skipped:</span>
                  <ul style={{ margin: 0, paddingLeft: '1.2rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                    {gbResult.skipped.map((s, i) => (
                      <li key={i}>{s.subject || '(unrecognised)'}{s.tier ? ` (${s.tier})` : ''} - {s.reason}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}

          <form onSubmit={handleIngestGradeBoundaries}>
            <div className="form-group" style={{ marginBottom: '1rem' }}>
              <label htmlFor="gb-board">Exam board</label>
              <select
                id="gb-board"
                className="form-control"
                value={gbBoard && gbLevel ? `${gbBoard}|||${gbLevel}` : ''}
                onChange={e => {
                  const [board, level] = e.target.value.split('|||');
                  setGbBoard(board || '');
                  setGbLevel(level || '');
                }}
                required
              >
                <option value="" disabled>Select board and level…</option>
                {boardLevelOptions.map(o => (
                  <option key={`${o.exam_board}|||${o.level}`} value={`${o.exam_board}|||${o.level}`}>
                    {o.exam_board} {o.level}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group" style={{ marginBottom: '1.5rem' }}>
              <label htmlFor="gb-file">Grade boundaries PDF</label>
              <input
                id="gb-file"
                type="file"
                accept=".pdf"
                className="form-control"
                style={{ padding: '0.5rem' }}
                onChange={e => setGbFile(e.target.files ? e.target.files[0] : null)}
                required
              />
            </div>

            <button
              type="submit"
              className="btn btn-primary"
              disabled={!gbBoard.trim() || !gbLevel.trim() || !gbFile || gbSubmitting}
              style={{ width: '100%', padding: '0.65rem' }}
            >
              {gbSubmitting ? 'Parsing document…' : 'Update grade boundaries'}
            </button>
          </form>
        </Modal>
      )}
    </div>
  );
}
