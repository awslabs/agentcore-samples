import React from 'react';
import { Chip } from '@mui/material';
import type { MatchStatus } from '../../../types/data.types';
import { getMatchStatusColor, getMatchStatusLabel } from '../../../utils/poCalculations';

interface MatchStatusCellProps {
  matchStatus: MatchStatus;
}

export const MatchStatusCell: React.FC<MatchStatusCellProps> = ({ matchStatus }) => {
  const color = getMatchStatusColor(matchStatus);
  const label = getMatchStatusLabel(matchStatus);

  return (
    <Chip
      label={label}
      size="small"
      sx={{
        backgroundColor: color,
        color: 'white',
        fontWeight: 600,
        minWidth: 90,
      }}
    />
  );
};
