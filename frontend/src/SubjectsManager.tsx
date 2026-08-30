import React, { useEffect, useState } from 'react';
import { useAuth } from './AuthContext';
import api, { extractErrorMessage } from './api';

export interface UserSubject {
  id: string;
  exam_board: string;
  level: string;
  subject: string;
}

interface SupportedCombo {
  level: string;
  exam_board: string;
  subject: string;
}

interface SubjectsManagerProps {
  onSubjectsChange?: (subjects: UserSubject[]) => void;
}

export default function SubjectsManager({ onSubjectsChange }: SubjectsManagerProps) {
  const { refreshSubjects } = useAuth();

  const [combos, setCombos] = useState<SupportedCombo[]>([]);
  const [level, setLevel] = useState('');
  const [examBoard, setExamBoard] = useState('');
  const [subject, setSubject] = useState('');
  const [subjects, setSubjects] = useState<UserSubject[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editLevel, setEditLevel] = useState('');
  const [editExamBoard, setEditExamBoard] = useState('');
  const [editSubject, setEditSubject] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const [subjectsRes, topicsRes] = await Promise.all([
          api.get('/auth/me/subjects'),
          api.get('/exams/topics'),
        ]);
        setSubjects(subjectsRes.data);
        onSubjectsChange?.(subjectsRes.data);

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
        if (supported.length > 0) {
          setLevel(supported[0].level);
          setExamBoard(supported[0].exam_board);
          setSubject(supported[0].subject);
        }
      } catch (err) {
        console.error('Failed to load subjects', err);
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const levelOptions = Array.from(new Set(combos.map(c => c.level)));
  const boardOptionsFor = (lvl: string) => Array.from(new Set(combos.filter(c => c.level === lvl).map(c => c.exam_board)));
  const subjectOptionsFor = (lvl: string, board: string) =>
    combos.filter(c => c.level === lvl && c.exam_board === board).map(c => c.subject);

  const handleLevelChange = (lvl: string) => {
    setLevel(lvl);
    const firstBoard = boardOptionsFor(lvl)[0];
    setExamBoard(firstBoard || '');
    const firstSubject = firstBoard ? subjectOptionsFor(lvl, firstBoard)[0] : undefined;
    setSubject(firstSubject || '');
  };

  const handleExamBoardChange = (board: string) => {
    setExamBoard(board);
    const firstSubject = subjectOptionsFor(level, board)[0];
    setSubject(firstSubject || '');
  };

  const handleEditLevelChange = (lvl: string) => {
    setEditLevel(lvl);
    const firstBoard = boardOptionsFor(lvl)[0];
    setEditExamBoard(firstBoard || '');
    const firstSubject = firstBoard ? subjectOptionsFor(lvl, firstBoard)[0] : undefined;
    setEditSubject(firstSubject || '');
  };

  const handleEditExamBoardChange = (board: string) => {
    setEditExamBoard(board);
    const firstSubject = subjectOptionsFor(editLevel, board)[0];
    setEditSubject(firstSubject || '');
  };

  const updateSubjects = (next: UserSubject[]) => {
    setSubjects(next);
    onSubjectsChange?.(next);
  };

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (!level || !examBoard || !subject) {
      setError('Select a level, exam board and subject');
      return;
    }
    try {
      const res = await api.post('/auth/me/subjects', { exam_board: examBoard, level, subject });
      updateSubjects([...subjects.filter(s => s.id !== res.data.id), res.data]);
      await refreshSubjects();
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to add subject'));
    }
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
    setError('');
  };

  const cancelEdit = () => setEditingId(null);

  const saveEdit = async (id: string) => {
    setError('');
    try {
      const res = await api.patch(`/auth/me/subjects/${id}`, {
        exam_board: editExamBoard,
        level: editLevel,
        subject: editSubject,
      });
      updateSubjects(subjects.map(s => (s.id === id ? res.data : s)));
      setEditingId(null);
      await refreshSubjects();
    } catch (err: any) {
      setError(extractErrorMessage(err, 'Failed to update subject'));
    }
  };

  if (loading) {
    return <p style={{ color: 'var(--text-muted)' }}>Loading subjects...</p>;
  }

  return (
    <div>
      {error && <div style={{ color: '#ef4444', marginBottom: '1rem' }}>{error}</div>}

      {combos.length === 0 ? (
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>No subjects are available on the platform yet.</p>
      ) : (
        <form onSubmit={handleAdd} style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: '1.5rem' }}>
          <div className="form-group" style={{ marginBottom: 0, flex: '1 1 120px' }}>
            <label>Level</label>
            <select value={level} onChange={e => handleLevelChange(e.target.value)} className="form-control">
              {levelOptions.map(l => <option key={l} value={l}>{l}</option>)}
            </select>
          </div>
          <div className="form-group" style={{ marginBottom: 0, flex: '1 1 120px' }}>
            <label>Exam board</label>
            <select value={examBoard} onChange={e => handleExamBoardChange(e.target.value)} className="form-control">
              {boardOptionsFor(level).map(b => <option key={b} value={b}>{b}</option>)}
            </select>
          </div>
          <div className="form-group" style={{ marginBottom: 0, flex: '2 1 200px' }}>
            <label>Subject</label>
            <select value={subject} onChange={e => setSubject(e.target.value)} className="form-control">
              {subjectOptionsFor(level, examBoard).map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <button type="submit" className="btn btn-primary" style={{ height: '44px' }}>
            Add Subject
          </button>
        </form>
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
                    <select value={editLevel} onChange={e => handleEditLevelChange(e.target.value)} className="form-control" style={{ width: 'auto' }}>
                      {levelOptions.map(l => <option key={l} value={l}>{l}</option>)}
                    </select>
                    <select value={editExamBoard} onChange={e => handleEditExamBoardChange(e.target.value)} className="form-control" style={{ width: 'auto' }}>
                      {boardOptionsFor(editLevel).map(b => <option key={b} value={b}>{b}</option>)}
                    </select>
                    <select value={editSubject} onChange={e => setEditSubject(e.target.value)} className="form-control" style={{ width: 'auto' }}>
                      {subjectOptionsFor(editLevel, editExamBoard).map(sub => <option key={sub} value={sub}>{sub}</option>)}
                    </select>
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
                  <span>{s.level} {s.exam_board} {s.subject}</span>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button onClick={() => startEdit(s)} className="btn btn-outline" style={{ padding: '0.2rem 0.6rem', fontSize: '0.8rem' }}>
                      Edit
                    </button>
                    <button onClick={() => handleRemove(s.id)} className="btn btn-outline" style={{ padding: '0.2rem 0.6rem', fontSize: '0.8rem' }}>
                      Remove
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
