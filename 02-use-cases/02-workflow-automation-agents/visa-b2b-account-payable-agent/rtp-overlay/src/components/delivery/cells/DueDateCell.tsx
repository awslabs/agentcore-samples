import React from 'react';
import { Box, Typography } from '@mui/material';
import WarningIcon from '@mui/icons-material/Warning';

interface DueDateCellProps {
  dueDate: Date;
  isOverdue: boolean;
}

export const DueDateCell: React.FC<DueDateCellProps> = ({ dueDate, isOverdue }) => {
  const today = new Date();
  const dueDateObj = new Date(dueDate);
  const daysUntilDue = Math.ceil((dueDateObj.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
  const isDueSoon = daysUntilDue >= 0 && daysUntilDue <= 3;

  const formattedDate = dueDateObj.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
      {isOverdue && <WarningIcon sx={{ fontSize: 18, color: 'error.main' }} />}
      <Typography
        variant="body2"
        sx={{
          color: isOverdue ? 'error.main' : isDueSoon ? 'warning.main' : 'text.primary',
          fontWeight: isOverdue || isDueSoon ? 700 : 400,
        }}
      >
        {formattedDate}
      </Typography>
    </Box>
  );
};
