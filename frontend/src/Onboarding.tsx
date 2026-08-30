import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from './AuthContext';
import SubjectsManager from './SubjectsManager';
import type { UserSubject } from './SubjectsManager';

export default function Onboarding() {
  const navigate = useNavigate();
  const { refreshSubjects } = useAuth();
  const [subjects, setSubjects] = useState<UserSubject[]>([]);

  const handleContinue = async () => {
    await refreshSubjects();
    navigate('/app');
  };

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem', maxWidth: '600px' }}>
      <h1 style={{ marginBottom: '0.5rem' }}>What are you studying?</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
        Add every subject and exam board you're taking - e.g. GCSE AQA Biology, or OCR A-Level Chemistry.
        You can change this later from Manage Account.
      </p>

      <div className="card" style={{ marginBottom: '2rem' }}>
        <SubjectsManager onSubjectsChange={setSubjects} />
      </div>

      <button
        onClick={handleContinue}
        className="btn btn-primary"
        disabled={subjects.length === 0}
        style={{ width: '100%' }}
      >
        Continue to practice
      </button>
      {subjects.length === 0 && (
        <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.75rem', textAlign: 'center' }}>
          Add at least one subject above to continue.
        </p>
      )}
    </div>
  );
}
