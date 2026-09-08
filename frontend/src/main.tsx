import { StrictMode, Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import HomepageOnlyShell from './HomepageOnlyShell'

// VITE_HOMEPAGE_ONLY=true builds ship just the marketing homepage (see
// HomepageOnlyShell/Landing) with no backend calls - useful for previewing
// or deploying the site before the rest of the product is hosted. FullApp
// (auth + practice + admin, all backend-dependent) is lazy-loaded so a
// homepage-only build never even fetches its chunk.
const HOMEPAGE_ONLY = import.meta.env.VITE_HOMEPAGE_ONLY === 'true';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {HOMEPAGE_ONLY ? (
      <HomepageOnlyShell />
    ) : (
      <Suspense fallback={null}>
        {(() => {
          const FullApp = lazy(() => import('./FullApp'));
          return <FullApp />;
        })()}
      </Suspense>
    )}
  </StrictMode>,
)
