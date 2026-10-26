import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import { AmplifyProvider } from './components/AmplifyProvider';
import './globals.css';
import '@aws-amplify/ui-react/styles.css';
import './amplify-theme.css';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export const metadata: Metadata = {
  title: process.env.APP_NAME || 'Next.js + Amplify Gen 2 Reference App',
  description: process.env.APP_DESCRIPTION || 'A sample base app for building full-stack projects with Next.js and AWS Amplify Gen 2',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {/* AmplifyProvider is a client component that configures Amplify
            with the generated outputs so auth works in the browser */}
        <AmplifyProvider>{children}</AmplifyProvider>
      </body>
    </html>
  );
}
