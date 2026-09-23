import React from 'react';
import { Box, Typography } from '@mui/material';
import { formatAmountDisplay, getRemainingAmountColor } from '../../../utils/poCalculations';

interface TotalRemainingCellProps {
  totalAmount: number;
  remainingAmount: number;
  currency: string;
}

export const TotalRemainingCell: React.FC<TotalRemainingCellProps> = ({
  totalAmount,
  remainingAmount,
  currency,
}) => {
  const color = getRemainingAmountColor(remainingAmount, totalAmount);
  const displayText = formatAmountDisplay(totalAmount, remainingAmount, currency);

  return (
    <Box>
      <Typography variant="body2" sx={{ fontWeight: 500 }}>
        {displayText}
      </Typography>
      <Typography
        variant="caption"
        sx={{
          color: color,
          fontWeight: 600,
        }}
      >
        {remainingAmount > 0 ? `${currency} ${remainingAmount.toLocaleString()} remaining` : 'Fully invoiced'}
      </Typography>
    </Box>
  );
};
