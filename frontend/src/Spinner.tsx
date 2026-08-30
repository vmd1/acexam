interface SpinnerProps {
  label?: string;
}

// Consistent loading indicator used across the app instead of bare "Loading..." text.
export default function Spinner({ label = 'Loading…' }: SpinnerProps) {
  return (
    <div className="loading-state" role="status" aria-live="polite">
      <div className="spinner" aria-hidden="true" />
      <p>{label}</p>
    </div>
  );
}
