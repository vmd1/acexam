import { AuthProvider } from './AuthContext';
import App from './App';

// Split out from main.tsx so it can be lazy-loaded and excluded entirely
// from a homepage-only build (see VITE_HOMEPAGE_ONLY in main.tsx) - this is
// the real product (auth, practice, admin) which needs the backend running.
export default function FullApp() {
  return (
    <AuthProvider>
      <App />
    </AuthProvider>
  );
}
