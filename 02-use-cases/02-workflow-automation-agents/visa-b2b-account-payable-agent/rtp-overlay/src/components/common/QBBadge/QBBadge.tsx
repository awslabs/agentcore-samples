import React from 'react';
import { Chip } from '@mui/material';
import type { ChipProps } from '@mui/material';
import { styled } from '@mui/material/styles';
import { colors } from '../../../theme/colors';

type QBBadgeColor = 'success' | 'error' | 'warning' | 'info' | 'default';

interface QBBadgeProps extends Omit<ChipProps, 'color'> {
  color?: QBBadgeColor;
}

const StyledChip = styled(Chip)<{ qbcolor: QBBadgeColor }>(({ qbcolor }) => ({
  fontSize: '12px',
  fontWeight: 500,
  height: '24px',
  borderRadius: '4px',
  
  ...(qbcolor === 'success' && {
    backgroundColor: colors.success.light,
    color: colors.success.dark,
  }),
  
  ...(qbcolor === 'error' && {
    backgroundColor: colors.error.light,
    color: colors.error.dark,
  }),
  
  ...(qbcolor === 'warning' && {
    backgroundColor: colors.warning.light,
    color: colors.warning.dark,
  }),
  
  ...(qbcolor === 'info' && {
    backgroundColor: colors.info.light,
    color: colors.info.dark,
  }),
  
  ...(qbcolor === 'default' && {
    backgroundColor: colors.gray[100],
    color: colors.text.primary,
  }),
}));

export const QBBadge: React.FC<QBBadgeProps> = ({ 
  color = 'default', 
  ...props 
}) => {
  return <StyledChip qbcolor={color} {...props} />;
};
