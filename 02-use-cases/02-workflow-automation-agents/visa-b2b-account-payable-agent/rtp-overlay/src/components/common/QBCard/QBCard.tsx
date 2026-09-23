import React from 'react';
import { Card, CardContent, CardHeader } from '@mui/material';
import { styled } from '@mui/material/styles';
import { colors } from '../../../theme/colors';
import { shadows } from '../../../theme/shadows';

interface QBCardProps {
  title?: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  noPadding?: boolean;
  sx?: any;
}

const StyledCard = styled(Card)({
  backgroundColor: colors.background.paper,
  borderRadius: '8px',
  boxShadow: shadows.sm,
  '&:hover': {
    boxShadow: shadows.base,
  },
});

const StyledCardHeader = styled(CardHeader)({
  borderBottom: `1px solid ${colors.border.light}`,
  '& .MuiCardHeader-title': {
    fontSize: '18px',
    fontWeight: 600,
    color: colors.text.primary,
  },
  '& .MuiCardHeader-subheader': {
    fontSize: '14px',
    color: colors.text.secondary,
  },
});

export const QBCard: React.FC<QBCardProps> = ({
  title,
  subtitle,
  actions,
  children,
  noPadding = false,
  sx,
}) => {
  return (
    <StyledCard sx={sx}>
      {(title || actions) && (
        <StyledCardHeader
          title={title}
          subheader={subtitle}
          action={actions}
        />
      )}
      <CardContent sx={{ padding: noPadding ? 0 : '20px' }}>
        {children}
      </CardContent>
    </StyledCard>
  );
};
