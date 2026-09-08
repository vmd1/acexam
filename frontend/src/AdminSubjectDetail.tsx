import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import api, { extractErrorMessage } from './api';
import Spinner from './Spinner';
import SubjectUploadForm from './SubjectUploadForm';
import SubjectPapersReview from './SubjectPapersReview';
import SubjectMisconceptions from './SubjectMisconceptions';
import { AlertCircle, ArrowLeft, Award, Bot, Check, FileStack, FileUp, Play, Plus, Save, Tag, Timer, Trash2, UploadCloud, X } from 'lucide-react';

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

interface GradeBoundaryRow {
  tier: string;
  grade: string;
  min_pct: number;
}

interface MarkingModelRow {
  exam_board: string;
  level: string;
  subject: string;
  tier: string;
  status: 'none' | 'training' | 'gated' | 'live';
  active_adapter_version: string | null;
  serving_port: number | null;
  eval_result: unknown;
  updated_at: string | null;
  accepted_training_examples: number;
}

// eval_result comes back from asyncpg as raw JSONB text (no decoded-object
// codec on the pool), same as images/answer_options elsewhere in this app.
function parseJsonMaybe<T>(value: unknown, fallback: T): T {
  if (value == null) return fallback;
  if (typeof value === 'object') return value as T;
  if (typeof value === 'string') {
    try { return JSON.parse(value); } catch { return fallback; }
  }
  return fallback;
}

interface EvalResult {
  n_test?: number;
  marks_awarded_exact_match_rate?: number;
  missed_points_overlap_f1_mean?: number;
  misconception_tags_overlap_f1_mean?: number;
  json_parse_rate?: number;
}

type TrainingJobStatus = 'queued' | 'assembling' | 'training' | 'evaluating' | 'done' | 'failed';

interface TrainingJobRow {
  id: string;
  exam_board: string;
  level: string;
  subject: string;
  tier: string;
  base_model: string;
  status: TrainingJobStatus;
  adapter_version: string | null;
  eval_result: unknown;
  log: string;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

const IN_FLIGHT_TRAINING_STATUSES: TrainingJobStatus[] = ['queued', 'assembling', 'training', 'evaluating'];

const MARKING_STATUSES: MarkingModelRow['status'][] = ['none', 'training', 'gated', 'live'];

function pct(v: number | undefined) {
  return v == null ? '—' : `${Math.round(v * 100)}%`;
}

export default function AdminSubjectDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [qualification, setQualification] = useState<Qualification | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [targetMarks, setTargetMarks] = useState('');
  const [timeLimitMinutes, setTimeLimitMinutes] = useState('');
  const [tiers, setTiers] = useState<string[]>([]);
  const [newTier, setNewTier] = useState('');
  const [savingSettings, setSavingSettings] = useState(false);
  const [savedSettings, setSavedSettings] = useState(false);

  const [markingModels, setMarkingModels] = useState<MarkingModelRow[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelDrafts, setModelDrafts] = useState<Record<string, { status: string; adapterVersion: string }>>({});
  const [savingTier, setSavingTier] = useState<string | null>(null);

  const [trainingJobs, setTrainingJobs] = useState<TrainingJobRow[]>([]);
  const [requestingTier, setRequestingTier] = useState<string | null>(null);
  const [expandedLogTier, setExpandedLogTier] = useState<string | null>(null);

  const [gradeBoundaries, setGradeBoundaries] = useState<GradeBoundaryRow[]>([]);
  const [gbTier, setGbTier] = useState('');
  const [gbDraft, setGbDraft] = useState<{ grade: string; min_pct: string }[]>([]);
  const [gbLoading, setGbLoading] = useState(true);
  const [gbSaving, setGbSaving] = useState(false);
  const [gbSaved, setGbSaved] = useState(false);

  const [deleting, setDeleting] = useState(false);

  const [specFile, setSpecFile] = useState<File | null>(null);
  const [specUpdating, setSpecUpdating] = useState(false);
  const [specResult, setSpecResult] = useState('');
  const [specError, setSpecError] = useState('');

  const [activeTab, setActiveTab] = useState<'overview' | 'upload' | 'papers' | 'misconceptions'>('overview');

  const load = async () => {
    if (!id) return;
    setLoading(true);
    setError('');
    try {
      const res = await api.get(`/admin/qualifications/${id}`);
      const q: Qualification = res.data;
      setQualification(q);
      setTargetMarks(q.custom_paper_target_marks != null ? String(q.custom_paper_target_marks) : '');
      setTimeLimitMinutes(q.custom_paper_time_limit_minutes != null ? String(q.custom_paper_time_limit_minutes) : '');
      setTiers(q.tiers);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load subject'));
    } finally {
      setLoading(false);
    }
  };

  const loadMarkingModels = async () => {
    if (!qualification) return;
    setModelsLoading(true);
    try {
      const res = await api.get('/admin/qualifications/marking-models');
      const fetched: MarkingModelRow[] = res.data.filter(
        (r: MarkingModelRow) =>
          r.exam_board === qualification.exam_board &&
          r.level === qualification.level &&
          r.subject === qualification.subject
      );
      const byTier = new Map(fetched.map(r => [r.tier, r]));

      // One row per tier this qualification actually has (§ Manage Subjects
      // tier editor), not just tiers that happen to have papers ingested
      // already - a tier's marking model can be configured/trained ahead
      // of its first paper. Any tier the backend returned that *isn't* in
      // the qualification's own tier list (e.g. a paper tagged with a tier
      // since removed there) is kept too, rather than silently dropped.
      const tierKeys = Array.from(new Set([...(qualification.tiers.length ? qualification.tiers : ['']), ...byTier.keys()]));
      const rows: MarkingModelRow[] = tierKeys.map(tier => byTier.get(tier) || {
        exam_board: qualification.exam_board,
        level: qualification.level,
        subject: qualification.subject,
        tier,
        status: 'none',
        active_adapter_version: null,
        serving_port: null,
        eval_result: null,
        updated_at: null,
        accepted_training_examples: 0,
      });

      setMarkingModels(rows);
      const drafts: Record<string, { status: string; adapterVersion: string }> = {};
      rows.forEach(r => {
        drafts[r.tier] = {
          status: r.status,
          adapterVersion: r.active_adapter_version || '',
        };
      });
      setModelDrafts(drafts);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load marking models'));
    } finally {
      setModelsLoading(false);
    }
  };

  const loadTrainingJobs = async () => {
    if (!qualification) return;
    try {
      const res = await api.get('/admin/qualifications/training-jobs', {
        params: {
          exam_board: qualification.exam_board,
          level: qualification.level,
          subject: qualification.subject,
        },
      });
      const jobs: TrainingJobRow[] = res.data;
      setTrainingJobs(jobs);
      // A job that just finished may have picked a new adapter version -
      // reflect it in the (still admin-editable) draft without clobbering
      // an in-progress manual edit for a different tier.
      setModelDrafts(prev => {
        const next = { ...prev };
        for (const tier of Object.keys(next)) {
          const latest = jobs.find(j => j.tier === tier && j.status === 'done');
          if (latest?.adapter_version) {
            next[tier] = { ...next[tier], adapterVersion: latest.adapter_version };
          }
        }
        return next;
      });
    } catch {
      // Non-fatal - the job panel just stays stale until the next successful poll.
    }
  };

  const loadGradeBoundaries = async () => {
    if (!qualification) return;
    setGbLoading(true);
    try {
      const res = await api.get(`/admin/qualifications/${qualification.id}/grade-boundaries`);
      setGradeBoundaries(res.data);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load grade boundaries'));
    } finally {
      setGbLoading(false);
    }
  };

  const gbTierOptions = qualification && qualification.tiers.length ? qualification.tiers : [''];

  // Draft rows always reflect whichever tier is selected - switching tiers
  // (or a fresh load) reloads the draft from the last-saved boundaries for
  // that tier, discarding any unsaved edits to the previous tier.
  useEffect(() => {
    setGbDraft(
      gradeBoundaries
        .filter(b => b.tier === gbTier)
        .map(b => ({ grade: b.grade, min_pct: String(b.min_pct) }))
    );
  }, [gbTier, gradeBoundaries]);

  const addGbRow = () => setGbDraft(prev => [...prev, { grade: '', min_pct: '' }]);
  const removeGbRow = (idx: number) => setGbDraft(prev => prev.filter((_, i) => i !== idx));
  const updateGbRow = (idx: number, field: 'grade' | 'min_pct', value: string) =>
    setGbDraft(prev => prev.map((r, i) => (i === idx ? { ...r, [field]: value } : r)));

  const saveGradeBoundaries = async () => {
    if (!qualification) return;
    setError('');
    const cleaned = gbDraft
      .map(r => ({ grade: r.grade.trim(), min_pct: Number(r.min_pct) }))
      .filter(r => r.grade !== '');
    if (cleaned.some(r => Number.isNaN(r.min_pct))) {
      setError('Every grade needs a numeric minimum %');
      return;
    }
    setGbSaving(true);
    setGbSaved(false);
    try {
      await api.put(`/admin/qualifications/${qualification.id}/grade-boundaries`, {
        tier: gbTier,
        boundaries: cleaned,
      });
      await loadGradeBoundaries();
      setGbSaved(true);
      setTimeout(() => setGbSaved(false), 2000);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to save grade boundaries'));
    } finally {
      setGbSaving(false);
    }
  };

  const latestJobForTier = (tier: string): TrainingJobRow | undefined =>
    trainingJobs.find(j => j.tier === tier);

  const requestTraining = async (tier: string) => {
    if (!qualification) return;
    setRequestingTier(tier);
    setError('');
    try {
      await api.post('/admin/qualifications/training-jobs', {
        exam_board: qualification.exam_board,
        level: qualification.level,
        subject: qualification.subject,
        tier,
      });
      await loadTrainingJobs();
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to request training'));
    } finally {
      setRequestingTier(null);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [id]);
  useEffect(() => {
    if (qualification) {
      loadMarkingModels();
      loadTrainingJobs();
      loadGradeBoundaries();
      setGbTier(prev => (qualification.tiers.includes(prev) ? prev : (qualification.tiers[0] || '')));
    }
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [qualification]);

  // While a training job is in flight, or a tier is live but the agent
  // hasn't reported a serving_port yet, poll both tables so status/log/port
  // updates show up on their own - the Mac agent writes these directly,
  // there's no other signal that tells this tab to refresh.
  const hasInFlightWork =
    trainingJobs.some(j => IN_FLIGHT_TRAINING_STATUSES.includes(j.status)) ||
    markingModels.some(r => r.status === 'live' && r.serving_port == null);

  useEffect(() => {
    if (!qualification || !hasInFlightWork) return;
    const interval = setInterval(() => { loadMarkingModels(); loadTrainingJobs(); }, 7000);
    return () => clearInterval(interval);
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [qualification, hasInFlightWork]);

  const updateSpec = async (e: FormEvent) => {
    e.preventDefault();
    if (!qualification || !specFile) return;
    setSpecUpdating(true);
    setSpecError('');
    setSpecResult('');

    const formData = new FormData();
    formData.append('file', specFile);
    formData.append('exam_board', qualification.exam_board);
    formData.append('level', qualification.level);
    formData.append('subject', qualification.subject);

    const marksWereUnset = qualification.custom_paper_target_marks == null && qualification.custom_paper_time_limit_minutes == null;

    try {
      const res = await api.post('/admin/ingestion/upload-spec', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      const filledMarks = marksWereUnset && res.data.target_marks != null && res.data.time_limit_minutes != null;
      setSpecResult(
        `Re-parsed ${res.data.topics_created} topics from the new specification.` +
        (filledMarks
          ? ` Also pre-filled custom paper settings from the spec: ${res.data.target_marks} marks, ${res.data.time_limit_minutes} min.`
          : '')
      );
      setSpecFile(null);
      await load();
    } catch (err) {
      setSpecError(extractErrorMessage(err, 'Failed to update specification'));
    } finally {
      setSpecUpdating(false);
    }
  };

  const saveSettings = async () => {
    if (!qualification) return;
    setSavingSettings(true);
    setSavedSettings(false);
    setError('');
    try {
      const res = await api.put(`/admin/qualifications/${qualification.id}`, {
        custom_paper_target_marks: targetMarks.trim() === '' ? null : Number(targetMarks),
        custom_paper_time_limit_minutes: timeLimitMinutes.trim() === '' ? null : Number(timeLimitMinutes),
        tiers,
      });
      setQualification({ ...qualification, ...res.data });
      setSavedSettings(true);
      setTimeout(() => setSavedSettings(false), 2000);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to save'));
    } finally {
      setSavingSettings(false);
    }
  };

  const addTier = () => {
    const t = newTier.trim();
    if (!t || tiers.includes(t)) return;
    setTiers(prev => [...prev, t]);
    setNewTier('');
  };

  const removeTier = (t: string) => {
    setTiers(prev => prev.filter(x => x !== t));
  };

  const saveMarkingModel = async (tier: string) => {
    if (!qualification) return;
    const draft = modelDrafts[tier];
    const existing = markingModels.find(r => r.tier === tier);
    setSavingTier(tier);
    setError('');
    try {
      await api.put('/admin/qualifications/marking-models', {
        exam_board: qualification.exam_board,
        level: qualification.level,
        subject: qualification.subject,
        tier,
        status: draft.status,
        active_adapter_version: draft.adapterVersion.trim() === '' ? null : draft.adapterVersion.trim(),
        // serving_port is agent-managed (backend/training/agent.py) - round-trip
        // whatever it last wrote rather than offering a manual override, so a
        // status-only edit here can never clobber it.
        serving_port: existing?.serving_port ?? null,
        eval_result: existing ? parseJsonMaybe(existing.eval_result, null) : null,
      });
      await loadMarkingModels();
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to save marking model status'));
    } finally {
      setSavingTier(null);
    }
  };

  const removeSubject = async () => {
    if (!qualification) return;
    if (!window.confirm(`Remove ${qualification.subject} (${qualification.level} ${qualification.exam_board}) from Manage Subjects?\n\nAlready-ingested papers and topics for it are kept - this only stops it being offered for new uploads or onboarding. Re-adding the subject later will link straight back to that existing content.`)) {
      return;
    }
    setDeleting(true);
    setError('');
    try {
      await api.delete(`/admin/qualifications/${qualification.id}`);
      navigate('/admin/subjects');
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to remove subject'));
      setDeleting(false);
    }
  };

  if (loading) {
    return <Spinner label="Loading subject…" />;
  }

  if (!qualification) {
    return (
      <div className="container" style={{ marginTop: '2.5rem' }}>
        <div className="banner banner-danger" role="alert">{error || 'Subject not found'}</div>
      </div>
    );
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem', maxWidth: '860px' }}>
      <Link to="/admin/subjects" className="subject-detail-back">
        <ArrowLeft size={15} aria-hidden="true" />
        All subjects
      </Link>

      <div className="subject-detail-header">
        <div>
          <div className="subject-detail-title">{qualification.subject}</div>
          <div className="subject-detail-meta">
            {qualification.level} · {qualification.exam_board} ·{' '}
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
              <FileStack size={14} aria-hidden="true" /> {qualification.paper_count} papers ingested
            </span>
          </div>
        </div>
      </div>

      <div className="subject-tabs" role="tablist" aria-label="Subject sections" style={{ display: 'flex', gap: '0.5rem', marginBottom: '2rem', flexWrap: 'wrap' }}>
        <button role="tab" aria-selected={activeTab === 'overview'} onClick={() => setActiveTab('overview')} className={`btn ${activeTab === 'overview' ? 'btn-primary' : 'btn-outline'}`}>
          Overview
        </button>
        <button role="tab" aria-selected={activeTab === 'upload'} onClick={() => setActiveTab('upload')} className={`btn ${activeTab === 'upload' ? 'btn-primary' : 'btn-outline'}`} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <UploadCloud size={15} aria-hidden="true" /> Upload paper
        </button>
        <button role="tab" aria-selected={activeTab === 'papers'} onClick={() => setActiveTab('papers')} className={`btn ${activeTab === 'papers' ? 'btn-primary' : 'btn-outline'}`} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <FileStack size={15} aria-hidden="true" /> Papers ({qualification.paper_count})
        </button>
        <button role="tab" aria-selected={activeTab === 'misconceptions'} onClick={() => setActiveTab('misconceptions')} className={`btn ${activeTab === 'misconceptions' ? 'btn-primary' : 'btn-outline'}`} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <Tag size={15} aria-hidden="true" /> Misconceptions
        </button>
      </div>

      {error && (
        <div className="banner banner-danger" role="alert" style={{ marginBottom: '1.5rem' }}>
          <span>{error}</span>
        </div>
      )}

      {activeTab === 'upload' && (
        <SubjectUploadForm qualification={qualification} onUploaded={load} />
      )}

      {activeTab === 'papers' && (
        <SubjectPapersReview qualification={qualification} />
      )}

      {activeTab === 'misconceptions' && (
        <SubjectMisconceptions qualification={qualification} />
      )}

      {activeTab === 'overview' && (
      <>
      <div className="subject-section">
        <div className="subject-section-heading"><Timer size={18} aria-hidden="true" /> Custom paper settings</div>
        <p className="subject-section-desc">
          The real paper's mark total and time allowance (e.g. 100 marks, 1h45) - what custom paper
          generation and its timer target. Leave blank to fall back to the default heuristic.
        </p>
        <div className="card" style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem', alignItems: 'flex-end' }}>
          <div className="form-group" style={{ margin: 0, width: '160px' }}>
            <label htmlFor="marks">Total marks</label>
            <input
              id="marks"
              type="number"
              min={1}
              className="form-control"
              placeholder="e.g. 100"
              value={targetMarks}
              onChange={e => setTargetMarks(e.target.value)}
            />
          </div>
          <div className="form-group" style={{ margin: 0, width: '180px' }}>
            <label htmlFor="time">Time limit (minutes)</label>
            <input
              id="time"
              type="number"
              min={1}
              className="form-control"
              placeholder="e.g. 105"
              value={timeLimitMinutes}
              onChange={e => setTimeLimitMinutes(e.target.value)}
            />
          </div>

          <div style={{ flex: 1, minWidth: '220px' }}>
            <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-secondary)', fontWeight: 500, fontSize: '0.9rem' }}>
              Tiers
            </label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', alignItems: 'center' }}>
              {tiers.map(t => (
                <span key={t} className="tier-editor-chip">
                  {t}
                  <button type="button" onClick={() => removeTier(t)} aria-label={`Remove tier ${t}`}>
                    <X size={13} aria-hidden="true" />
                  </button>
                </span>
              ))}
              <input
                type="text"
                value={newTier}
                onChange={e => setNewTier(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addTier(); } }}
                placeholder="Add tier…"
                className="form-control"
                style={{ width: '130px', padding: '0.35rem 0.6rem', fontSize: '0.85rem' }}
              />
              <button type="button" className="btn btn-outline" onClick={addTier} style={{ padding: '0.35rem 0.6rem' }} aria-label="Add tier">
                <Plus size={14} aria-hidden="true" />
              </button>
            </div>
          </div>

          <button
            className="btn btn-primary"
            disabled={savingSettings}
            onClick={saveSettings}
            style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}
          >
            {savedSettings ? <Check size={16} aria-hidden="true" /> : <Save size={16} aria-hidden="true" />}
            {savingSettings ? 'Saving…' : savedSettings ? 'Saved' : 'Save'}
          </button>
        </div>
      </div>

      <div className="subject-section">
        <div className="subject-section-heading"><Award size={18} aria-hidden="true" /> Grade boundaries</div>
        <p className="subject-section-desc">
          Minimum overall % needed for each predicted grade, per tier - drives the "Predicted grade" shown
          on the student analytics page. Boundaries are tier-specific (e.g. Higher and Foundation differ),
          so pick a tier below before editing. No boundaries configured means the predicted grade shows
          "N/A" instead of a guess.
        </p>
        <div className="card">
          {gbLoading ? (
            <Spinner label="Loading grade boundaries…" />
          ) : (
            <>
              <div className="form-group" style={{ margin: '0 0 1rem 0', maxWidth: '220px' }}>
                <label htmlFor="gb-tier">Tier</label>
                <select
                  id="gb-tier"
                  className="form-control"
                  value={gbTier}
                  onChange={e => setGbTier(e.target.value)}
                >
                  {gbTierOptions.map(t => (
                    <option key={t} value={t}>{t || '(no tier)'}</option>
                  ))}
                </select>
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginBottom: '1rem' }}>
                {gbDraft.length === 0 && (
                  <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', margin: 0 }}>
                    No boundaries configured for this tier yet.
                  </p>
                )}
                {gbDraft.map((row, idx) => (
                  <div key={idx} style={{ display: 'flex', gap: '0.6rem', alignItems: 'center' }}>
                    <input
                      type="text"
                      className="form-control"
                      style={{ width: '120px' }}
                      placeholder="Grade (e.g. 9)"
                      value={row.grade}
                      onChange={e => updateGbRow(idx, 'grade', e.target.value)}
                    />
                    <input
                      type="number"
                      className="form-control"
                      style={{ width: '160px' }}
                      placeholder="Min % (e.g. 85)"
                      min={0}
                      max={100}
                      value={row.min_pct}
                      onChange={e => updateGbRow(idx, 'min_pct', e.target.value)}
                    />
                    <button
                      type="button"
                      className="btn btn-outline"
                      onClick={() => removeGbRow(idx)}
                      aria-label={`Remove grade ${row.grade || idx}`}
                      style={{ padding: '0.4rem 0.6rem' }}
                    >
                      <X size={14} aria-hidden="true" />
                    </button>
                  </div>
                ))}
                <button type="button" className="btn btn-outline" onClick={addGbRow} style={{ alignSelf: 'flex-start', display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                  <Plus size={14} aria-hidden="true" /> Add grade
                </button>
              </div>

              <button
                className="btn btn-primary"
                disabled={gbSaving}
                onClick={saveGradeBoundaries}
                style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}
              >
                {gbSaved ? <Check size={16} aria-hidden="true" /> : <Save size={16} aria-hidden="true" />}
                {gbSaving ? 'Saving…' : gbSaved ? 'Saved' : 'Save boundaries'}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="subject-section">
        <div className="subject-section-heading"><FileUp size={18} aria-hidden="true" /> Specification</div>
        <p className="subject-section-desc">
          Re-parses the topic hierarchy and tiers from a new spec document, in place - the existing
          spec_code entries are updated, not duplicated, so already-ingested papers and their topic
          links are unaffected. Use this when an exam board revises a specification.
        </p>
        <div className="card">
          {specError && (
            <div className="banner banner-danger" role="alert" style={{ marginBottom: '1rem' }}>
              <AlertCircle size={16} />
              <span>{specError}</span>
            </div>
          )}
          {specResult && (
            <div className="banner banner-success" role="status" style={{ marginBottom: '1rem' }}>
              <span>{specResult}</span>
            </div>
          )}
          <form onSubmit={updateSpec} style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem', alignItems: 'flex-end' }}>
            <div className="form-group" style={{ margin: 0, flex: '1 1 260px' }}>
              <label htmlFor="spec-file">Specification PDF</label>
              <input
                id="spec-file"
                type="file"
                accept=".pdf"
                className="form-control"
                style={{ padding: '0.5rem' }}
                onChange={e => setSpecFile(e.target.files ? e.target.files[0] : null)}
              />
            </div>
            <button type="submit" className="btn btn-primary" disabled={!specFile || specUpdating}>
              {specUpdating ? 'Updating…' : 'Update specification'}
            </button>
          </form>
        </div>
      </div>

      <div className="subject-section">
        <div className="subject-section-heading"><Bot size={18} aria-hidden="true" /> AI marking models</div>
        <p className="subject-section-desc">
          Rollout gate for §6.3/§6.4 self-hosted marking. Only a tier set to <strong>live</strong> has its
          3+ mark questions AI-marked - anything else falls back to manual review. Click "Request training"
          to run the pipeline; once trained, flip a tier to <strong>live</strong> and the Mac agent starts
          serving it and reports its port here automatically.
        </p>
        <div className="card">
          {modelsLoading ? (
            <Spinner label="Loading marking models…" />
          ) : markingModels.length === 0 ? (
            <p style={{ color: 'var(--text-secondary)', margin: 0 }}>
              Add a tier above to configure a marking model for it.
            </p>
          ) : (
            markingModels.map(row => {
              const draft = modelDrafts[row.tier] || { status: 'none', adapterVersion: '' };
              const ev = parseJsonMaybe<EvalResult>(row.eval_result, {});
              const job = latestJobForTier(row.tier);
              const jobInFlight = !!job && IN_FLIGHT_TRAINING_STATUSES.includes(job.status);
              return (
                <div key={row.tier} className="marking-model-row-wrap">
                  <div className="marking-model-row">
                    <div style={{ minWidth: '110px' }}>
                      <div style={{ fontWeight: 700 }}>{row.tier || '(no tier)'}</div>
                      <span className={`status-badge status-${row.status}`}>{row.status}</span>
                    </div>

                    <div className="marking-model-metrics" style={{ minWidth: '200px', flex: '1 1 200px' }}>
                      <span>{row.accepted_training_examples} training examples</span>
                      {ev.n_test != null && (
                        <>
                          <span>marks match <strong>{pct(ev.marks_awarded_exact_match_rate)}</strong></span>
                          <span>missed points F1 <strong>{pct(ev.missed_points_overlap_f1_mean)}</strong></span>
                        </>
                      )}
                    </div>

                    <select
                      className="form-control"
                      style={{ width: '120px' }}
                      value={draft.status}
                      onChange={e => setModelDrafts(prev => ({ ...prev, [row.tier]: { ...draft, status: e.target.value } }))}
                    >
                      {MARKING_STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>

                    <input
                      type="text"
                      className="form-control"
                      style={{ width: '160px' }}
                      placeholder="adapter version"
                      value={draft.adapterVersion}
                      onChange={e => setModelDrafts(prev => ({ ...prev, [row.tier]: { ...draft, adapterVersion: e.target.value } }))}
                    />

                    <div style={{ width: '150px', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                      {row.serving_port != null
                        ? <>port <strong style={{ color: 'var(--text-primary)' }}>{row.serving_port}</strong></>
                        : row.status === 'live' ? 'waiting for agent…' : '—'}
                    </div>

                    <button
                      className="btn btn-primary"
                      disabled={savingTier === row.tier}
                      onClick={() => saveMarkingModel(row.tier)}
                      style={{ padding: '0.5rem 0.8rem' }}
                    >
                      {savingTier === row.tier ? 'Saving…' : 'Save'}
                    </button>

                    <button
                      className="btn btn-outline"
                      disabled={jobInFlight || requestingTier === row.tier}
                      onClick={() => requestTraining(row.tier)}
                      style={{ padding: '0.5rem 0.8rem', display: 'flex', alignItems: 'center', gap: '0.35rem' }}
                    >
                      <Play size={14} aria-hidden="true" />
                      {requestingTier === row.tier ? 'Requesting…' : jobInFlight ? 'Training…' : 'Request training'}
                    </button>
                  </div>

                  {job && (
                    <div className="marking-model-job" style={{ marginTop: '0.5rem' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', fontSize: '0.85rem' }}>
                        <span className={`status-badge status-${job.status === 'failed' ? 'none' : job.status === 'done' ? 'gated' : 'training'}`}>
                          {job.status}
                        </span>
                        <span style={{ color: 'var(--text-secondary)' }}>
                          requested {new Date(job.created_at).toLocaleString()}
                        </span>
                        {job.log && (
                          <button
                            type="button"
                            className="btn-link"
                            style={{ fontSize: '0.85rem' }}
                            onClick={() => setExpandedLogTier(prev => prev === row.tier ? null : row.tier)}
                          >
                            {expandedLogTier === row.tier ? 'Hide log' : 'Show log'}
                          </button>
                        )}
                      </div>
                      {job.status === 'failed' && job.error && (
                        <div className="banner banner-danger" role="alert" style={{ marginTop: '0.4rem' }}>
                          <AlertCircle size={16} />
                          <span>{job.error}</span>
                        </div>
                      )}
                      {expandedLogTier === row.tier && job.log && (
                        <pre className="marking-model-log">{job.log.slice(-2000)}</pre>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>

      <div className="subject-section">
        <div className="subject-section-heading" style={{ color: 'var(--danger)' }}>Danger zone</div>
        <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontWeight: 700 }}>Remove this subject</div>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
              Stops it being offered for new uploads or onboarding. Already-ingested content is kept.
            </div>
          </div>
          <button
            className="btn btn-outline"
            disabled={deleting}
            onClick={removeSubject}
            style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: 'var(--danger)', borderColor: 'var(--danger)' }}
          >
            <Trash2 size={16} aria-hidden="true" />
            {deleting ? 'Removing…' : 'Remove subject'}
          </button>
        </div>
      </div>
      </>
      )}
    </div>
  );
}
