'use client';

import { Amplify } from 'aws-amplify';
import outputs from '../../../amplify_outputs.json';

// Configure Amplify on the client side — this must run in a client component
// so the auth tokens and session management work in the browser.
Amplify.configure(outputs, { ssr: true });

export function AmplifyProvider({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
