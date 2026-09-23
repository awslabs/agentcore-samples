import React from 'react';
import { Button } from '@mui/material';
import { styled } from '@mui/material/styles';
import { colors } from '../../../theme/colors';

type QBButtonVariant = 'primary' | 'secondary' | 'text' | 'danger';

interface QBButtonProps extends Omit<React.ComponentProps<typeof Button>, 'variant'> {
  variant?: QBButtonVariant;
}

const StyledButton = styled(Button)<{ qbvariant: QBButtonVariant }>(({ qbvariant }) => ({
  textTransform: 'none',
  fontWeight: 500,
  borderRadius: '4px',
  padding: '8px 16px',
  
  ...(qbvariant === 'primary' && {
    backgroundColor: colors.primary.main,
    color: '#FFFFFF',
    '&:hover': {
      backgroundColor: colors.primary.dark,
    },
  }),
  
  ...(qbvariant === 'secondary' && {
    backgroundColor: '#FFFFFF',
    color: colors.primary.main,
    border: `1px solid ${colors.primary.main}`,
    '&:hover': {
      backgroundColor: colors.primary.hover,
    },
  }),
  
  ...(qbvariant === 'text' && {
    backgroundColor: 'transparent',
    color: colors.primary.main,
    '&:hover': {
      backgroundColor: colors.primary.hover,
    },
  }),
  
  ...(qbvariant === 'danger' && {
    backgroundColor: colors.error.main,
    color: '#FFFFFF',
    '&:hover': {
      backgroundColor: colors.error.dark,
    },
  }),
}));

export const QBButton: React.FC<QBButtonProps> = ({ 
  variant = 'primary', 
  children, 
  ...props 
}) => {
  return (
    <StyledButton qbvariant={variant} {...props}>
      {children}
    </StyledButton>
  );
};
