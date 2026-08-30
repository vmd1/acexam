import { useNavigate } from 'react-router-dom';
import { Sparkles } from 'lucide-react';

export default function PracticeSetup() {
  const navigate = useNavigate();

  const startAdaptive = () => {
    navigate('/app/session', { state: { mode: 'adaptive' } });
  };

  return (
    <div className="container" style={{ marginTop: '2.5rem', marginBottom: '4rem', maxWidth: '900px' }}>
      <h1 style={{ marginBottom: '0.5rem' }}>Start practicing</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
        Let the adaptive engine target your weakest topics, active misconceptions, and decaying memory -
        or head to Custom Papers to build a topic-filtered mock paper instead.
      </p>

      <div className="card" style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: '1rem',
      }}>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start' }}>
          <span className="hero-stat-icon" aria-hidden="true"><Sparkles size={20} /></span>
          <div>
            <h3 style={{ marginBottom: '0.25rem' }}>Adaptive practice</h3>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', margin: 0 }}>
              Automatically targets your weakest topics, active misconceptions, and decaying memory.
            </p>
          </div>
        </div>
        <button className="btn btn-primary" onClick={startAdaptive}>Start adaptive session</button>
      </div>
    </div>
  );
}
