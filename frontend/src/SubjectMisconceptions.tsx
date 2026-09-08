import { useEffect, useState } from 'react';
import api, { extractErrorMessage } from './api';
import EmptyState from './EmptyState';
import { CheckCircle2, Tag } from 'lucide-react';

interface Qualification {
  exam_board: string;
  level: string;
  subject: string;
}

interface Props {
  qualification: Qualification;
}

// §6.2a canonical misconception taxonomy, scoped to one subject via
// GET /admin/ingestion/misconceptions's exam_board/level/subject filter
// (joins through spec_topics, since misconception_taxonomy only stores a
// bare spec_code).
export default function SubjectMisconceptions({ qualification }: Props) {
  const [misconceptions, setMisconceptions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<{ type: 'success' | 'danger'; text: string } | null>(null);
  const [approvingAll, setApprovingAll] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      const res = await api.get('/admin/ingestion/misconceptions', {
        params: {
          exam_board: qualification.exam_board,
          level: qualification.level,
          subject: qualification.subject,
        },
      });
      setMisconceptions(res.data);
    } catch (err) {
      console.error('Failed to load misconceptions', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qualification.exam_board, qualification.level, qualification.subject]);

  const handleApprove = async (spec_code: string, tag_id: string) => {
    try {
      await api.post('/admin/ingestion/misconception/approve', { spec_code, tag_id });
      setNotice({ type: 'success', text: 'Misconception tag approved.' });
      fetchData();
    } catch (err) {
      setNotice({ type: 'danger', text: 'Failed to approve misconception.' });
    }
  };

  const unapproved = misconceptions.filter((m: any) => !m.approved_at);

  const handleApproveAll = async () => {
    if (unapproved.length === 0) return;
    setApprovingAll(true);
    try {
      await api.post('/admin/ingestion/misconception/approve-all', {
        tags: unapproved.map((m: any) => ({ spec_code: m.spec_code, tag_id: m.tag_id })),
      });
      setNotice({ type: 'success', text: `Approved ${unapproved.length} misconception tag${unapproved.length === 1 ? '' : 's'}.` });
      fetchData();
    } catch (err) {
      setNotice({ type: 'danger', text: extractErrorMessage(err, 'Failed to approve misconceptions.') });
    } finally {
      setApprovingAll(false);
    }
  };

  if (loading) {
    return <div className="card"><p style={{ color: 'var(--text-secondary)', margin: 0 }}>Loading misconceptions…</p></div>;
  }

  return (
    <div className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap' }}>
        <div>
          <h3>Canonical misconception taxonomy</h3>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
            Pre-approved taxonomy of misconception tags for this subject's spec points, keeping tags
            stable for student memory tracking (§3.7).
          </p>
        </div>
        {unapproved.length > 0 && (
          <button
            onClick={handleApproveAll}
            disabled={approvingAll}
            className="btn btn-primary"
            style={{ padding: '0.4rem 0.85rem', fontSize: '0.85rem', whiteSpace: 'nowrap' }}
          >
            {approvingAll ? 'Approving…' : `Approve all (${unapproved.length})`}
          </button>
        )}
      </div>

      {notice && (
        <div className={`banner banner-${notice.type}`} role="alert" style={{ marginBottom: '1.5rem' }}>
          <span>{notice.text}</span>
        </div>
      )}

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
                    onClick={() => handleApprove(m.spec_code, m.tag_id)}
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
  );
}
