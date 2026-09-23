import React, { useMemo } from 'react';
import { Box } from '@mui/material';
import { styled } from '@mui/material/styles';
import ShoppingCartIcon from '@mui/icons-material/ShoppingCart';
import ReceiptIcon from '@mui/icons-material/Receipt';
import LocalShippingIcon from '@mui/icons-material/LocalShipping';
import VerifiedIcon from '@mui/icons-material/Verified';
import AccountBalanceIcon from '@mui/icons-material/AccountBalance';
import PaymentIcon from '@mui/icons-material/Payment';
import { QBSidebar } from '../../common/QBSidebar';
import { useAuth } from '../../../contexts/AuthContext';
import { colors } from '../../../theme/colors';

interface AppLayoutProps {
  children: React.ReactNode;
}

const LayoutContainer = styled(Box)({
  display: 'flex',
  minHeight: '100vh',
  backgroundColor: colors.background.default,
});

const MainContent = styled(Box)({
  flexGrow: 1,
  minWidth: 0,
  marginLeft: '16px',
  boxSizing: 'border-box',
});

export const AppLayout: React.FC<AppLayoutProps> = ({ children }) => {
  const { role } = useAuth();

  const navSections = useMemo(() => {
    const sections = [];

    // Receiving role
    if (role === 'receiving') {
      sections.push({
        title: 'Delivery',
        items: [
          {
            label: 'Goods Receipt',
            path: '/delivery',
            icon: <LocalShippingIcon />,
          },
          {
            label: 'Purchase Orders',
            path: '/delivery/purchase-orders',
            icon: <ShoppingCartIcon />,
          },
        ],
      });
    }

    // QC role
    if (role === 'qc') {
      sections.push({
        title: 'Quality Control',
        items: [
          {
            label: 'QC Dashboard',
            path: '/qc',
            icon: <VerifiedIcon />,
          },
        ],
      });
    }

    // Treasury role
    if (role === 'treasury') {
      sections.push({
        title: 'Treasury',
        items: [
          {
            label: 'Treasury Dashboard',
            path: '/treasury',
            icon: <AccountBalanceIcon />,
          },
          {
            label: 'Purchase Orders',
            path: '/delivery/purchase-orders',
            icon: <ShoppingCartIcon />,
          },
          {
            label: 'Invoices',
            path: '/invoices',
            icon: <ReceiptIcon />,
          },
          {
            label: 'Payments',
            path: '/payments',
            icon: <PaymentIcon />,
          },
        ],
      });
    }

    return sections;
  }, [role]);

  return (
    <LayoutContainer>
      <QBSidebar sections={navSections} />
      <MainContent>{children}</MainContent>
    </LayoutContainer>
  );
};
