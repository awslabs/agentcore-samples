'use client';

import { useEffect, useState } from 'react';
import { getCurrentUser } from 'aws-amplify/auth';
import { useRouter } from 'next/navigation';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

/**
 * ProtectedRoute is a client-side route guard.
 *
 * On mount it calls getCurrentUser() which reads the session tokens that
 * Amplify stores in browser storage. If the tokens are valid the user is
 * considered authenticated and children are rendered. If the call throws
 * (no session, expired token, etc.) the user is redirected to the home
 * page where the Authenticator UI is shown.
 *
 * A loading state is shown while the async check is in flight so the
 * protected content is never briefly visible to unauthenticated users.
 */
export function ProtectedRoute({ children }: ProtectedRouteProps): React.JSX.Element {
  const router = useRouter();
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function checkAuth() {
      try {
        // getCurrentUser() resolves if a valid session exists in storage.
        // It throws UserUnAuthenticatedException (or similar) otherwise.
        await getCurrentUser();
        setAuthenticated(true);
      } catch {
        // Any error means the user is not authenticated — redirect to sign-in
        router.push('/app');
      } finally {
        // Always clear the loading state so the UI can update
        setLoading(false);
      }
    }

    checkAuth();
  }, [router]); // re-run if the router instance changes (e.g. navigation)

  // Show a loading indicator while the auth check is in progress
  if (loading) {
    return (
      <div role="status" aria-label="Checking authentication" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: '#fff' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <svg className="animate-spin" style={{ width: 24, height: 24, color: '#7c3aed' }} viewBox="0 0 24 24" fill="none">
            <circle style={{ opacity: 0.25 }} cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path style={{ opacity: 0.75 }} fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <span style={{ fontSize: '1rem', color: '#6b7280' }}>Loading...</span>
        </div>
      </div>
    );
  }

  // Render nothing while the redirect is happening
  if (!authenticated) {
    return <></>;
  }

  return <>{children}</>;
}
