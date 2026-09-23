import React from 'react';
import { Box, Chip } from '@mui/material';
import WarningIcon from '@mui/icons-material/Warning';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';

interface VarianceIndicatorProps {
  variance: number; // percentage
  showIcon?: boolean;
  showText?: boolean;
}

export const VarianceIndicator: React.FC<VarianceIndicatorProps> = ({
  variance,
  showIcon = true,
  showText = true,
}) => {
  const absVariance = Math.abs(variance);

  // Determine color based on variance
  const getColor = () => {
    if (absVariance < 5) return 'success';
    if (absVariance <= 10) return 'warning';
    return 'error';
  };

  const getIcon = () => {
    if (absVariance < 5) return <CheckCircleIcon fontSize="small" />;
    if (absVariance <= 10) return <WarningIcon fontSize="small" />;
    return <ErrorIcon fontSize="small" />;
  };

  const color = getColor();
  const formattedVariance = `${variance > 0 ? '+' : ''}${variance.toFixed(2)}%`;

  if (!showText && showIcon) {
    return (
      <Box
        sx={{
          display: 'inline-flex',
          alignItems: 'center',
          color: `${color}.main`,
        }}
        aria-label={`Variance: ${formattedVariance}`}
      >
        {getIcon()}
      </Box>
    );
  }

  return (
    <Chip
      icon={showIcon ? getIcon() : undefined}
      label={formattedVariance}
      color={color}
      size="small"
      sx={{ fontWeight: 500 }}
      aria-label={`Variance: ${formattedVariance}`}
    />
  );
};
