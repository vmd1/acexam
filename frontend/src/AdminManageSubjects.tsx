import { useEffect, useState } from 'react';
import api, { extractErrorMessage } from './api';
import Spinner from './Spinner';
import EmptyState from './EmptyState';
import { Check, ClipboardX, Save } from 'lucide-react';

interface Qualification {
  id: string;
  exam_board: string;
  level: string;
  subject: string;
  tiers: string[];
  custom_paper_target_marks: number | null;
  custom_paper_time_limit_minutes: number | null;
}

// Local editable copy of a row's two configurable fields, kept as strings
// so an input can be legitimately empty (= "not configured yet", falls
// back to /generate/custom-paper's own heuristic) without fighting a
// number input's handling of blank values.
interface DraftRow {
  targetMarks: string;
  timeLimitMinutes: string;
}

export default function AdminManageSubjects() {
  const [qualifications, setQualifications] = useState<Qualification[]>([]);
  const [drafts, setDrafts] = useState<Record<string, DraftRow>>({});
  const [loading, setLoading] = useState(true);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await api.get('/admin/qualifications');
      const quals: Qualification[] = res.data;
      setQualifications(quals);
      const nextDrafts: Record<string, DraftRow> = {};
      quals.forEach(q => {
        nextDrafts[q.id] = {
          targetMarks: q.custom_paper_target_marks != null ? String(q.custom_paper_target_marks) : '',
          timeLimitMinutes: q.custom_paper_time_limit_minutes != null ? String(q.custom_paper_time_limit_minutes) : '',
        };
      });
      setDrafts(nextDrafts);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to load qualifications'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const updateDraft = (id: string, field: keyof DraftRow, value: string) => {
    setDrafts(prev => ({ ...prev, [id]: { ...prev[id], [field]: value } }));
  };

  const save = async (q: Qualification) => {
    const draft = drafts[q.id];
    setSavingId(q.id);
    setSavedId(null);
    setError('');
    try {
      const res = await api.put(`/admin/qualifications/${q.id}`, {
        custom_paper_target_marks: draft.targetMarks.trim() === '' ? null : Number(draft.targetMarks),
        custom_paper_time_limit_minutes: draft.timeLimitMinutes.trim() === '' ? null : Number(draft.timeLimitMinutes),
      });
      setQualifications(prev => prev.map(x => (x.id === q.id ? res.data : x)));
      setSavedId(q.id);
      setTimeout(() => setSavedId(cur => (cur === q.id ? null : cur)), 2000);
    } catch (err) {
      setError(extractErrorMessage(err, 'Failed to save'));
    } finally {
      setSavingId(null);
    }
  };

  if (loading) {
    return <Spinner label="Loading qualifications…" />;
  }

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem', maxWidth: '900px' }}>
      <h1 style={{ marginBottom: '0.5rem' }}>Manage subjects</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
        Set the real paper's mark total and time allowance per qualification (e.g. AQA GCSE Biology
        Higher: 100 marks, 1h45) - this is what custom paper generation and its timer target. Leave a
        field blank to fall back to the default heuristic instead.
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
          description="Upload a specification document first to create qualifications to configure."
        />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {qualifications.map(q => {
            const draft = drafts[q.id] || { targetMarks: '', timeLimitMinutes: '' };
            return (
              <div key={q.id} className="card" style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem', alignItems: 'flex-end' }}>
                <div style={{ minWidth: '200px', flex: '1 1 200px' }}>
                  <div style={{ fontWeight: 700 }}>{q.subject}</div>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                    {q.level} {q.exam_board}{q.tiers.length > 0 ? ` · ${q.tiers.join('/')}` : ''}
                  </div>
                </div>

                <div className="form-group" style={{ margin: 0, width: '140px' }}>
                  <label htmlFor={`marks-${q.id}`}>Total marks</label>
                  <input
                    id={`marks-${q.id}`}
                    type="number"
                    min={1}
                    className="form-control"
                    placeholder="e.g. 100"
                    value={draft.targetMarks}
                    onChange={e => updateDraft(q.id, 'targetMarks', e.target.value)}
                  />
                </div>

                <div className="form-group" style={{ margin: 0, width: '160px' }}>
                  <label htmlFor={`time-${q.id}`}>Time limit (minutes)</label>
                  <input
                    id={`time-${q.id}`}
                    type="number"
                    min={1}
                    className="form-control"
                    placeholder="e.g. 105"
                    value={draft.timeLimitMinutes}
                    onChange={e => updateDraft(q.id, 'timeLimitMinutes', e.target.value)}
                  />
                </div>

                <button
                  className="btn btn-primary"
                  disabled={savingId === q.id}
                  onClick={() => save(q)}
                  style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}
                >
                  {savedId === q.id ? <Check size={16} aria-hidden="true" /> : <Save size={16} aria-hidden="true" />}
                  {savingId === q.id ? 'Saving…' : savedId === q.id ? 'Saved' : 'Save'}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
