import React from 'react';
import { Box } from '@mui/material';
import { styled } from '@mui/material/styles';
import { colors } from '../../../theme/colors';
import { spacing } from '../../../theme/spacing';

interface ContentAreaProps {
  children: React.ReactNode;
}

const StyledContentArea = styled(Box)({
  padding: spacing[6],
  backgroundColor: colors.background.default,
  minHeight: '100vh',
  boxSizing: 'border-box',
});

export const ContentArea: React.FC<ContentAreaProps> = ({ children }) => {
  return <StyledContentArea>{children}</StyledContentArea>;
};
