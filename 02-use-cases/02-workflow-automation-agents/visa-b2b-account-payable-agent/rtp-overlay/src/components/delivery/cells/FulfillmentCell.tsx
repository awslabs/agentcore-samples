import React from 'react';
import { Box, Typography, LinearProgress } from '@mui/material';
import { getFulfillmentColor } from '../../../utils/poCalculations';

interface FulfillmentCellProps {
  orderedQuantity: number;
  receivedQuantity: number;
  fulfillmentPercentage: number;
}

export const FulfillmentCell: React.FC<FulfillmentCellProps> = ({
  orderedQuantity,
  receivedQuantity,
  fulfillmentPercentage,
}) => {
  const color = getFulfillmentColor(fulfillmentPercentage);

  return (
    <Box sx={{ minWidth: 120 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 0.5 }}>
        <Typography variant="body2" sx={{ fontWeight: 500, mr: 1 }}>
          {Math.round(fulfillmentPercentage)}%
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {receivedQuantity} / {orderedQuantity}
        </Typography>
      </Box>
      <LinearProgress
        variant="determinate"
        value={Math.min(fulfillmentPercentage, 100)}
        sx={{
          height: 6,
          borderRadius: 3,
          backgroundColor: 'grey.200',
          '& .MuiLinearProgress-bar': {
            backgroundColor: color,
            borderRadius: 3,
          },
        }}
      />
    </Box>
  );
};
