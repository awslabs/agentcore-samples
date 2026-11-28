import type { Metadata } from 'next';
import { AmplifyProvider } from './components/AmplifyProvider';
import './globals.css';
import '@aws-amplify/ui-react/styles.css';
import './amplify-theme.css';

// Use system fonts: production builds must not depend on a Google Fonts request.

export const metadata: Metadata = {
  title: process.env.APP_NAME || 'Next.js + Amplify Gen 2 Reference App',
  description: process.env.APP_DESCRIPTION || 'A sample base app for building full-stack projects with Next.js and AWS Amplify Gen 2',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* AmplifyProvider is a client component that configures Amplify
            with the generated outputs so auth works in the browser */}
        <AmplifyProvider>{children}</AmplifyProvider>
      </body>
    </html>
  );
}
