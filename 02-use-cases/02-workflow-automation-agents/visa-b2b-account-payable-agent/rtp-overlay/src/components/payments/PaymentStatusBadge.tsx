import React from 'react';
import { Chip } from '@mui/material';
import {
  HourglassEmpty as PendingIcon,
  CreditCard as CardIssuedIcon,
  CheckCircle as CompletedIcon,
  Error as FailedIcon,
  Send as SubmittedIcon,
} from '@mui/icons-material';

interface PaymentStatusBadgeProps {
  status: string;
  paymentMethod?: string;
  size?: 'small' | 'medium';
}

export const PaymentStatusBadge: React.FC<PaymentStatusBadgeProps> = ({
  status,
  paymentMethod,
  size = 'small',
}) => {
  const getStatusConfig = () => {
    switch (status.toLowerCase()) {
      case 'pending':
        return {
          label: 'Pending',
          color: '#FFA726' as const,
          icon: <PendingIcon />,
        };
      case 'card_issued':
        return {
          label: 'Card Issued',
          color: '#1A1F71' as const, // Visa blue
          icon: <CardIssuedIcon />,
        };
      case 'submitted':
        return {
          label: 'Submitted',
          color: '#42A5F5' as const,
          icon: <SubmittedIcon />,
        };
      case 'completed':
        return {
          label: 'Completed',
          color: '#66BB6A' as const,
          icon: <CompletedIcon />,
        };
      case 'failed':
        return {
          label: 'Failed',
          color: '#EF5350' as const,
          icon: <FailedIcon />,
        };
      default:
        return {
          label: status,
          color: '#9E9E9E' as const,
          icon: <PendingIcon />,
        };
    }
  };

  const config = getStatusConfig();

  const getPaymentMethodLabel = () => {
    if (!paymentMethod) return '';
    return paymentMethod === 'visa_b2b' ? ' (Visa B2B)' : ' (ISO20022)';
  };

  return (
    <Chip
      icon={config.icon}
      label={config.label + getPaymentMethodLabel()}
      size={size}
      sx={{
        backgroundColor: config.color,
        color: 'white',
        fontWeight: 600,
        '& .MuiChip-icon': {
          color: 'white',
        },
      }}
    />
  );
};
