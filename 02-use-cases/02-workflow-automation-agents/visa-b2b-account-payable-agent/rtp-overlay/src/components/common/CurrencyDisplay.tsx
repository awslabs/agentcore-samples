import React from 'react';
import { Typography } from '@mui/material';
import type { TypographyProps } from '@mui/material';

interface CurrencyDisplayProps {
  amount: number;
  currency?: string;
  variant?: TypographyProps['variant'];
  color?: TypographyProps['color'];
}

export const CurrencyDisplay: React.FC<CurrencyDisplayProps> = ({
  amount,
  currency = 'USD',
  variant = 'body1',
  color,
}) => {
  const formatCurrency = (value: number, currencyCode: string): string => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currencyCode,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  };

  return (
    <Typography variant={variant} color={color} component="span">
      {formatCurrency(amount, currency)}
    </Typography>
  );
};
