import { redirect } from 'next/navigation';

// Root page redirects to /app (Authenticator → Assistant)
export default function Home() {
  redirect('/app');
}
