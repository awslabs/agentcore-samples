import React from 'react';
import { Chip } from '@mui/material';
import type { PaymentStatus } from '../../types/payment.types';
import { paymentStatusColors } from '../../theme/paymentColors';

interface PaymentStatusChipProps {
  status: PaymentStatus;
  size?: 'small' | 'medium';
}

export const PaymentStatusChip: React.FC<PaymentStatusChipProps> = ({
  status,
  size = 'medium',
}) => {
  const colors = paymentStatusColors[status];

  return (
    <Chip
      label={status.charAt(0).toUpperCase() + status.slice(1)}
      size={size}
      sx={{
        backgroundColor: colors.light,
        color: colors.dark,
        fontWeight: 500,
        textTransform: 'capitalize',
        borderRadius: '4px',
      }}
      aria-label={`Payment status: ${status}`}
    />
  );
};
