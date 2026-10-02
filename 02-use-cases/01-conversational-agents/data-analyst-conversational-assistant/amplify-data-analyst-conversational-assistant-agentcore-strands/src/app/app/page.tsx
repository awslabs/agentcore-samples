// Server component — reads env vars and passes to client components
import { AppPageClient } from './AppPageClient';

export default function AppPage() {
  const appName = process.env.APP_NAME || 'Next.js + Amplify Gen 2 Reference App';
  return <AppPageClient appName={appName} />;
}
