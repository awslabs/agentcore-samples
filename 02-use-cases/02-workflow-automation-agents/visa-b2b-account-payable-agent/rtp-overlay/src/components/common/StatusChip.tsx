import React from 'react';
import { Chip } from '@mui/material';
import type { PaymentStatus } from '../../types/invoice.types';

interface StatusChipProps {
  status: PaymentStatus;
  size?: 'small' | 'medium';
}

const statusConfig: Record<
  PaymentStatus,
  { label: string; color: 'default' | 'primary' | 'secondary' | 'error' | 'info' | 'success' | 'warning' }
> = {
  pending: { label: 'Pending', color: 'default' },
  processing: { label: 'Processing', color: 'info' },
  generated: { label: 'Generated', color: 'success' },
  sent: { label: 'Sent', color: 'secondary' },
  paid: { label: 'Paid', color: 'success' },
  failed: { label: 'Failed', color: 'error' },
};

export const StatusChip: React.FC<StatusChipProps> = ({ status, size = 'small' }) => {
  const config = statusConfig[status];

  return (
    <Chip
      label={config.label}
      color={config.color}
      size={size}
      sx={{ fontWeight: 500 }}
      aria-label={`Payment status: ${config.label}`}
    />
  );
};
