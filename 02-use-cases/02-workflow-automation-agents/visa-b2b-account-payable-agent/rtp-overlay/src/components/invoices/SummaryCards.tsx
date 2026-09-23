import React from 'react';
import { Box } from '@mui/material';
import ReceiptIcon from '@mui/icons-material/Receipt';
import AttachMoneyIcon from '@mui/icons-material/AttachMoney';
import PendingActionsIcon from '@mui/icons-material/PendingActions';
import WarningIcon from '@mui/icons-material/Warning';
import { QBMetricCard } from '../common/QBMetricCard';
import type { InvoiceSummary } from '../../types/invoice.types';

interface SummaryCardsProps {
  summary: InvoiceSummary | null;
  loading: boolean;
}



export const SummaryCards: React.FC<SummaryCardsProps> = ({ summary, loading }) => {
  const formatCurrency = (amount: number): string => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(amount);
  };

  if (loading) {
    return <Box sx={{ mb: 4 }}>Loading...</Box>;
  }

  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', md: 'repeat(4, 1fr)' },
        gap: 3,
        mb: 4,
      }}
    >
      <QBMetricCard
        title="Total Invoices"
        value={summary?.totalInvoices || 0}
        icon={<ReceiptIcon />}
      />
      <QBMetricCard
        title="Total Amount"
        value={summary ? formatCurrency(summary.totalAmount) : '$0'}
        icon={<AttachMoneyIcon />}
      />
      <QBMetricCard
        title="Pending Payments"
        value={summary?.pendingPayments || 0}
        icon={<PendingActionsIcon />}
      />
      <QBMetricCard
        title="Overdue Invoices"
        value={summary?.overdueInvoices || 0}
        icon={<WarningIcon />}
      />
    </Box>
  );
};
