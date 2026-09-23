import React from 'react';
import { AppLayout } from '../layout/AppLayout';
import { ContentArea } from '../layout/ContentArea';

interface LayoutProps {
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  return (
    <AppLayout>
      <ContentArea>{children}</ContentArea>
    </AppLayout>
  );
};
