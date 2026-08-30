import React, { useEffect, useMemo, useState } from 'react';
import { useAuth } from './AuthContext';
import api, { extractErrorMessage } from './api';
import Spinner from './Spinner';
import EmptyState from './EmptyState';
import Modal from './Modal';
import { iconForSubject } from './subjectIcons';
import { AlertCircle, BookOpen, Pencil, Trash2, Plus, X, Check } from 'lucide-react';

export interface UserSubject {
  id: string;
  exam_board: string;
  level: string;
  subject: string;
  tier?: string | null;
}

interface SupportedCombo {
  level: string;
  exam_board: string;
  subject: string;
}

interface Qualification {
  exam_board: string;
  level: string;
  subject: string;
  tiers: string[];
}

interface StagedSubject {
  level: string;
  exam_board: string;
  subject: string;
  tier?: string | null;
}

interface SubjectsManagerProps {
  onSubjectsChange?: (subjects: UserSubject[]) => void;
}

export default function SubjectsManager({ onSubjectsChange }: SubjectsManagerProps) {
  const { refreshSubjects } = useAuth();

  const [combos, setCombos] = useState<SupportedCombo[]>([]);
  const [qualifications, setQualifications] = useState<Qualification[]>([]);
  const [subjects, setSubjects] = useState<UserSubject[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editLevel, setEditLevel] = useState('');
  const [editExamBoard, setEditExamBoard] = useState('');
  const [editSubject, setEditSubject] = useState('');
  const [editTier, setEditTier] = useState('');

  // Picker modal state
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerLevel, setPickerLevel] = useState('');
  const [pendingBoardChoice, setPendingBoardChoice] = useState<string | null>(null);
  const [pendingTierChoice, setPendingTierChoice] = useState<{ subject: string; board: string } | null>(null);
  const [staged, setStaged] = useState<StagedSubject[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [subjectsRes, topicsRes, qualificationsRes] = await Promise.all([
          api.get('/auth/me/subjects'),
          api.get('/exams/topics'),
          api.get('/exams/qualifications'),
        ]);
        setSubjects(subjectsRes.data);
        onSubjectsChange?.(subjectsRes.data);
        setQualifications(qualificationsRes.data);

        const seen = new Set<string>();
        const supported: SupportedCombo[] = [];
        (topicsRes.data as any[]).forEach(t => {
          const key = `${t.level}::${t.exam_board}::${t.subject}`;
          if (!seen.has(key)) {
            seen.add(key);
            supported.push({ level: t.level, exam_board: t.exam_board, subject: t.subject });
          }
        });
        setCombos(supported);
        if (supported.length > 0) setPickerLevel(supported[0].level);
      } catch (err) {
        console.error('Failed to load subjects', err);
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const tiersFor = (lvl: string, board: string, subjectName: string): string[] => {
    const q = qualifications.find(q => q.level === lvl && q.exam_board === board && q.subject === subjectName);
    return q?.tiers ?? [];
  };

  const levelOptions = Array.from(new Set(combos.map(c => c.level)));
  const boardOptionsFor = (lvl: string) => Array.from(new Set(combos.filter(c => c.level === lvl).map(c => c.exam_board)));
  const subjectOptionsFor = (lvl: string, board: string) =>
    combos.filter(c => c.level === lvl && c.exam_board === board).map(c => c.subject);

  const handleEditLevelChange = (lvl: string) => {
    setEditLevel(lvl);
    const firstBoard = boardOptionsFor(lvl)[0];
    setEditExamBoard(firstBoard || '');
    const firstSubject = firstBoard ? subjectOptionsFor(lvl, firstBoard)[0] : undefined;
    setEditSubject(firstSubject || '');
    setEditTier(firstBoard && firstSubject ? (tiersFor(lvl, firstBoard, firstSubject)[0] || '') : '');
  };

  const handleEditExamBoardChange = (board: string) => {
    setEditExamBoard(board);
    const firstSubject = subjectOptionsFor(editLevel, board)[0];
    setEditSubject(firstSubject || '');
    setEditTier(firstSubject ? (tiersFor(editLevel, board, firstSubject)[0] || '') : '');
  };

  const handleEditSubjectChange = (subjectName: string) => {
    setEditSubject(subjectName);
    setEditTier(tiersFor(editLevel, editExamBoard, subjectName)[0] || '');
  };

  const updateSubjects = (next: UserSubject[]) => {
    setSubjects(next);
    onSubjectsChange?.(next);
  };

  const handleRemove = async (id: string) => {
    try {
      await api.delete(`/auth/me/subjects/${id}`);
      updateSubjects(subjects.filter(s => s.id !== id));
      await refreshSubjects();
    } catch (err) {
      console.error('Failed to remove subject', err);
    }
  };

  const startEdit = (s: UserSubject) => {
    setEditingId(s.id);
    setEditLevel(s.level);
    setEditExamBoard(s.exam_board);
    setEditSubject(s.subject);
    setEditTier(s.tier || '');
    setError('');
  };

  const cancelEdit = () => setEditingId(null);

  const saveEdit = async (id: string) => {
    setError('');
    try {
      const tierOptions = tiersFor(editLevel, editExamBoard, editSubject);
      const res = await api.patch(`/auth/me/subjects/${id}`, {
        exam_board: editExamBoard,
        level: editLevel,
        subject: editSubject,
        tier: tierOptions.length > 0 ? (editTier || tierOptions[0]) : null,
      });
      updateSubjects(subjects.map(s => (s.id === id ? res.data : s)));
      setEditingId(null);
      await refreshSubjects();
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to update subject'));
    }
  };

  // ---------- Picker modal logic ----------

  const subjectNamesForLevel = useMemo(() => {
    return Array.from(new Set(combos.filter(c => c.level === pickerLevel).map(c => c.subject)));
  }, [combos, pickerLevel]);

  const boardsForSubject = (lvl: string, subjectName: string) =>
    Array.from(new Set(combos.filter(c => c.level === lvl && c.subject === subjectName).map(c => c.exam_board)));

  // A given (level, board, subject) may support several tiers - each an
  // independent thing a student can add (e.g. Higher AND Foundation, though
  // in practice a student would only ever pick one). "Already added" /
  // "fully added" need to account for tier so a subject doesn't look
  // fully-added when there's still an un-added tier variant for a board.
  const remainingTierChoicesFor = (lvl: string, board: string, subjectName: string): (string | null)[] => {
    const tiers = tiersFor(lvl, board, subjectName);
    const options: (string | null)[] = tiers.length > 0 ? tiers : [null];
    return options.filter(tier =>
      !subjects.some(s => s.level === lvl && s.exam_board === board && s.subject === subjectName && (s.tier || null) === tier)
    );
  };

  const isAlreadyAdded = (lvl: string, board: string, subjectName: string) =>
    remainingTierChoicesFor(lvl, board, subjectName).length === 0;

  const isFullyAdded = (lvl: string, subjectName: string) =>
    boardsForSubject(lvl, subjectName).every(board => isAlreadyAdded(lvl, board, subjectName));

  const isStaged = (lvl: string, subjectName: string) =>
    staged.some(s => s.level === lvl && s.subject === subjectName);

  const openPicker = () => {
    setStaged([]);
    setPendingBoardChoice(null);
    setPendingTierChoice(null);
    setError('');
    setPickerOpen(true);
  };

  const closePicker = () => {
    setPickerOpen(false);
    setPendingBoardChoice(null);
    setPendingTierChoice(null);
  };

  const stageWithTierResolved = (board: string, subjectName: string) => {
    const remainingTiers = tiersFor(pickerLevel, board, subjectName).filter(tier =>
      !subjects.some(s => s.level === pickerLevel && s.exam_board === board && s.subject === subjectName && s.tier === tier)
    );

    if (remainingTiers.length > 1) {
      setPendingTierChoice({ subject: subjectName, board });
      return;
    }

    setStaged(prev => [...prev, {
      level: pickerLevel,
      exam_board: board,
      subject: subjectName,
      tier: remainingTiers[0] ?? null,
    }]);
    setPendingBoardChoice(null);
    setPendingTierChoice(null);
  };

  const handleTileClick = (subjectName: string) => {
    if (isFullyAdded(pickerLevel, subjectName)) return;

    if (isStaged(pickerLevel, subjectName)) {
      setStaged(prev => prev.filter(s => !(s.level === pickerLevel && s.subject === subjectName)));
      setPendingBoardChoice(null);
      setPendingTierChoice(null);
      return;
    }

    const availableBoards = boardsForSubject(pickerLevel, subjectName).filter(
      b => !isAlreadyAdded(pickerLevel, b, subjectName)
    );

    if (availableBoards.length === 1) {
      stageWithTierResolved(availableBoards[0], subjectName);
    } else {
      setPendingBoardChoice(subjectName);
    }
  };

  const chooseBoardForPending = (board: string) => {
    if (!pendingBoardChoice) return;
    stageWithTierResolved(board, pendingBoardChoice);
  };

  const chooseTierForPending = (tier: string) => {
    if (!pendingTierChoice) return;
    setStaged(prev => [...prev, {
      level: pickerLevel,
      exam_board: pendingTierChoice.board,
      subject: pendingTierChoice.subject,
      tier,
    }]);
    setPendingBoardChoice(null);
    setPendingTierChoice(null);
  };

  const unstage = (index: number) => {
    setStaged(prev => prev.filter((_, i) => i !== index));
  };

  const handleSaveStaged = async () => {
    if (staged.length === 0) return;
    setSaving(true);
    setError('');
    try {
      const results = await Promise.all(
        staged.map(s => api.post('/auth/me/subjects', s))
      );
      const newOnes = results.map(r => r.data);
      updateSubjects([...subjects.filter(s => !newOnes.some((n: any) => n.id === s.id)), ...newOnes]);
      await refreshSubjects();
      closePicker();
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to add subjects'));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <Spinner label="Loading your subjects…" />;
  }

  return (
    <div>
      {error && !pickerOpen && (
        <div className="banner banner-danger" role="alert" style={{ marginBottom: '1rem' }}>
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      )}

      {combos.length === 0 ? (
        <EmptyState
          icon={<BookOpen size={36} strokeWidth={1.5} />}
          title="No subjects available yet"
          description="The platform doesn't have any subjects published yet — check back soon."
        />
      ) : (
        <div style={{ marginBottom: '1.5rem' }}>
          <button onClick={openPicker} className="btn btn-primary">
            <Plus size={16} aria-hidden="true" />
            Add subjects
          </button>
        </div>
      )}

      {subjects.length === 0 && combos.length > 0 && (
        <EmptyState
          icon={<BookOpen size={36} strokeWidth={1.5} />}
          title="You haven't added a subject yet"
          description="Add one above to start practicing and tracking your progress."
        />
      )}

      {subjects.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {subjects.map(s => (
            <div key={s.id} style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '0.6rem 0.9rem',
              borderRadius: '8px',
              background: 'var(--bg-tertiary)',
              border: '1px solid var(--border)',
              gap: '0.75rem',
              flexWrap: 'wrap',
            }}>
              {editingId === s.id ? (
                <>
                  <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', flex: 1 }}>
                    <select aria-label="Level" value={editLevel} onChange={e => handleEditLevelChange(e.target.value)} className="form-control" style={{ width: 'auto' }}>
                      {levelOptions.map(l => <option key={l} value={l}>{l}</option>)}
                    </select>
                    <select aria-label="Exam board" value={editExamBoard} onChange={e => handleEditExamBoardChange(e.target.value)} className="form-control" style={{ width: 'auto' }}>
                      {boardOptionsFor(editLevel).map(b => <option key={b} value={b}>{b}</option>)}
                    </select>
                    <select aria-label="Subject" value={editSubject} onChange={e => handleEditSubjectChange(e.target.value)} className="form-control" style={{ width: 'auto' }}>
                      {subjectOptionsFor(editLevel, editExamBoard).map(sub => <option key={sub} value={sub}>{sub}</option>)}
                    </select>
                    {tiersFor(editLevel, editExamBoard, editSubject).length > 0 && (
                      <select aria-label="Tier" value={editTier} onChange={e => setEditTier(e.target.value)} className="form-control" style={{ width: 'auto' }}>
                        {tiersFor(editLevel, editExamBoard, editSubject).map(tier => <option key={tier} value={tier}>{tier}</option>)}
                      </select>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button onClick={() => saveEdit(s.id)} className="btn btn-primary" style={{ padding: '0.2rem 0.6rem', fontSize: '0.8rem' }}>
                      Save
                    </button>
                    <button onClick={cancelEdit} className="btn btn-outline" style={{ padding: '0.2rem 0.6rem', fontSize: '0.8rem' }}>
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    {React.createElement(iconForSubject(s.subject), { size: 16, 'aria-hidden': true, style: { color: 'var(--accent-color)', flexShrink: 0 } })}
                    {s.level} {s.exam_board} {s.subject}{s.tier ? ` (${s.tier})` : ''}
                  </span>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button
                      onClick={() => startEdit(s)}
                      className="btn btn-outline"
                      style={{ padding: '0.2rem 0.6rem', fontSize: '0.8rem' }}
                      aria-label={`Edit ${s.level} ${s.exam_board} ${s.subject}`}
                    >
                      <Pencil size={14} aria-hidden="true" />
                      Edit
                    </button>
                    <button
                      onClick={() => handleRemove(s.id)}
                      className="btn btn-outline"
                      style={{ padding: '0.2rem 0.6rem', fontSize: '0.8rem' }}
                      aria-label={`Remove ${s.level} ${s.exam_board} ${s.subject}`}
                    >
                      <Trash2 size={14} aria-hidden="true" />
                      Remove
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}

      {pickerOpen && (
        <Modal title="Add subjects" onClose={closePicker}>
          {error && (
            <div className="banner banner-danger" role="alert" style={{ marginBottom: '1rem' }}>
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}

          <div className="level-tabs" role="tablist" aria-label="Level">
            {levelOptions.map(lvl => (
              <button
                key={lvl}
                role="tab"
                aria-selected={pickerLevel === lvl}
                className={`level-tab${pickerLevel === lvl ? ' active' : ''}`}
                onClick={() => { setPickerLevel(lvl); setPendingBoardChoice(null); setPendingTierChoice(null); }}
              >
                {lvl}
              </button>
            ))}
          </div>

          <div className="subject-grid">
            {subjectNamesForLevel.map(subjectName => {
              const Icon = iconForSubject(subjectName);
              const fullyAdded = isFullyAdded(pickerLevel, subjectName);
              const selected = isStaged(pickerLevel, subjectName) || fullyAdded;
              return (
                <button
                  key={subjectName}
                  className={`subject-tile${selected ? ' selected' : ''}`}
                  onClick={() => handleTileClick(subjectName)}
                  disabled={fullyAdded}
                  aria-pressed={selected}
                  title={fullyAdded ? `${subjectName} — already added` : subjectName}
                >
                  <Icon size={18} aria-hidden="true" />
                  <span>{subjectName}</span>
                  {selected && <Check size={14} aria-hidden="true" style={{ marginLeft: 'auto' }} />}
                </button>
              );
            })}
          </div>

          {pendingBoardChoice && (
            <div className="board-picker">
              <strong>Choose your exam board</strong>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '0.25rem' }}>
                For {pendingBoardChoice} ({pickerLevel})
              </p>
              <div className="board-picker-options">
                {boardsForSubject(pickerLevel, pendingBoardChoice)
                  .filter(b => !isAlreadyAdded(pickerLevel, b, pendingBoardChoice))
                  .map(board => (
                    <button key={board} className="board-picker-btn" onClick={() => chooseBoardForPending(board)}>
                      {board}
                    </button>
                  ))}
              </div>
            </div>
          )}

          {pendingTierChoice && (
            <div className="board-picker">
              <strong>Choose your tier</strong>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '0.25rem' }}>
                For {pendingTierChoice.subject} ({pendingTierChoice.board}, {pickerLevel})
              </p>
              <div className="board-picker-options">
                {tiersFor(pickerLevel, pendingTierChoice.board, pendingTierChoice.subject)
                  .filter(tier => !subjects.some(s =>
                    s.level === pickerLevel && s.exam_board === pendingTierChoice.board &&
                    s.subject === pendingTierChoice.subject && s.tier === tier
                  ))
                  .map(tier => (
                    <button key={tier} className="board-picker-btn" onClick={() => chooseTierForPending(tier)}>
                      {tier}
                    </button>
                  ))}
              </div>
            </div>
          )}

          {staged.length > 0 && (
            <div className="staged-chips">
              {staged.map((s, i) => (
                <span key={`${s.level}-${s.exam_board}-${s.subject}-${s.tier ?? ''}`} className="staged-chip">
                  {s.exam_board} {s.level} {s.subject}{s.tier ? ` (${s.tier})` : ''}
                  <button onClick={() => unstage(i)} aria-label={`Remove ${s.subject} from selection`}>
                    <X size={12} aria-hidden="true" />
                  </button>
                </span>
              ))}
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button
              onClick={handleSaveStaged}
              disabled={staged.length === 0 || saving}
              className="btn btn-primary"
            >
              {saving ? 'Saving…' : `Save ${staged.length} subject${staged.length === 1 ? '' : 's'}`}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
