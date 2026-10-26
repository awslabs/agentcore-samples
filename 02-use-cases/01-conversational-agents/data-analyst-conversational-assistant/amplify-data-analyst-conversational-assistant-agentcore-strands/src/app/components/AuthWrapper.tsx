'use client';

import { Authenticator, ThemeProvider, createTheme } from '@aws-amplify/ui-react';

interface AuthWrapperProps {
  children: React.ReactNode;
  appName?: string;
}

// ThemeProvider controls heading font size at the token level — most reliable approach
// per official docs: https://ui.docs.amplify.aws/react/connected-components/authenticator/customization
const theme = createTheme({
  name: 'app-auth-theme',
  tokens: {
    components: {
      heading: {
        3: {
          fontSize: { value: '1.125rem' },
          fontWeight: { value: '600' },
        },
      },
    },
  },
});

export function AuthWrapper({ children, appName = 'Next.js + Amplify Gen 2 Reference App' }: AuthWrapperProps): React.JSX.Element {
  const currentYear = new Date().getFullYear();

  const components = {
    Header() {
      return (
        <div className="auth-header">
          <h1 className="auth-app-name">{appName}</h1>
        </div>
      );
    },

    Footer() {
      return (
        <div className="auth-footer">
          <p className="auth-copyright">
            © {currentYear}, Amazon Web Services, Inc. or its affiliates. All rights reserved.
          </p>
        </div>
      );
    },
  };

  return (
    <ThemeProvider theme={theme}>
      <div className="auth-page-container">
        <Authenticator loginMechanisms={['email']} components={components}>
          {() => <>{children}</>}
        </Authenticator>
      </div>
    </ThemeProvider>
  );
}
