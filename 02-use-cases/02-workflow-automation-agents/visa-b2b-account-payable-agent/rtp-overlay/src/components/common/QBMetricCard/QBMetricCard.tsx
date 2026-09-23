import React from 'react';
import { Card, CardContent, Box, Typography } from '@mui/material';
import { styled } from '@mui/material/styles';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import { colors } from '../../../theme/colors';
import { shadows } from '../../../theme/shadows';
import { spacing } from '../../../theme/spacing';

interface QBMetricCardProps {
  title: string;
  value: string | number;
  trend?: {
    value: number;
    direction: 'up' | 'down';
  };
  icon: React.ReactNode;
  onClick?: () => void;
}

const StyledCard = styled(Card, {
  shouldForwardProp: (prop) => prop !== 'clickable',
})<{ clickable: boolean }>(({ clickable }) => ({
  backgroundColor: '#f0f9f4',
  borderRadius: '8px',
  boxShadow: shadows.sm,
  cursor: clickable ? 'pointer' : 'default',
  transition: 'box-shadow 0.2s ease, transform 0.2s ease',
  '&:hover': clickable
    ? {
        boxShadow: shadows.md,
        transform: 'translateY(-2px)',
      }
    : {},
}));

const IconContainer = styled(Box)({
  width: '48px',
  height: '48px',
  borderRadius: '8px',
  backgroundColor: colors.primary.hover,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  color: colors.primary.main,
  '& svg': {
    fontSize: '24px',
  },
});

const TrendContainer = styled(Box)<{ direction: 'up' | 'down' }>(({ direction }) => ({
  display: 'flex',
  alignItems: 'center',
  gap: spacing[1],
  color: direction === 'up' ? colors.success.main : colors.error.main,
  fontSize: '14px',
  fontWeight: 500,
}));

export const QBMetricCard: React.FC<QBMetricCardProps> = ({
  title,
  value,
  trend,
  icon,
  onClick,
}) => {
  return (
    <StyledCard clickable={!!onClick} onClick={onClick}>
      <CardContent sx={{ padding: spacing[5] }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <Box sx={{ flex: 1 }}>
            <Typography
              variant="body2"
              sx={{
                fontSize: '14px',
                fontWeight: 500,
                color: colors.text.secondary,
                marginBottom: spacing[2],
              }}
            >
              {title}
            </Typography>
            <Typography
              variant="h2"
              sx={{
                fontSize: '32px',
                fontWeight: 600,
                color: colors.text.primary,
                marginBottom: spacing[1],
              }}
            >
              {value}
            </Typography>
            {trend && (
              <TrendContainer direction={trend.direction}>
                {trend.direction === 'up' ? (
                  <TrendingUpIcon sx={{ fontSize: '18px' }} />
                ) : (
                  <TrendingDownIcon sx={{ fontSize: '18px' }} />
                )}
                <span>{Math.abs(trend.value)}%</span>
              </TrendContainer>
            )}
          </Box>
          <IconContainer>{icon}</IconContainer>
        </Box>
      </CardContent>
    </StyledCard>
  );
};
