/**
 * Main Layout Component
 * Provides consistent layout structure for all pages
 */

import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../../contexts/MockAuthContext';
import Navigation from './Navigation';
import { oilGasTheme } from '../../styles/oilGasTheme';

interface MainLayoutProps {
  children: React.ReactNode;
}

const MainLayout: React.FC<MainLayoutProps> = ({ children }) => {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        backgroundColor: oilGasTheme.colors.background,
        fontFamily: oilGasTheme.typography.fontFamily,
      }}
    >
      <Navigation />
      <main
        style={{
          maxWidth: '1400px',
          margin: '0 auto',
          padding: oilGasTheme.spacing.xl,
        }}
      >
        {children}
      </main>
    </div>
  );
};

export default MainLayout;
