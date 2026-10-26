'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthenticator } from '@aws-amplify/ui-react';
import { AuthWrapper } from '../components/AuthWrapper';

function AppContent() {
  const { authStatus } = useAuthenticator((context) => [context.authStatus]);
  const router = useRouter();

  useEffect(() => {
    if (authStatus === 'authenticated') {
      router.push('/app/assistant');
    }
  }, [authStatus, router]);

  // Render nothing while redirect is in flight
  return null;
}

export function AppPageClient({ appName }: { appName: string }) {
  return (
    <AuthWrapper appName={appName}>
      <AppContent />
    </AuthWrapper>
  );
}
