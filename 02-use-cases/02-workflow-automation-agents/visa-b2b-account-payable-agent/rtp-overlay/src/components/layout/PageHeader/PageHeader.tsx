import React from 'react';
import { Box, Typography } from '@mui/material';
import { styled } from '@mui/material/styles';
import { colors } from '../../../theme/colors';
import { spacing } from '../../../theme/spacing';

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}

const HeaderContainer = styled(Box)({
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  marginBottom: spacing[6],
  paddingBottom: spacing[4],
  borderBottom: `1px solid ${colors.border.light}`,
});

const TitleSection = styled(Box)({
  display: 'flex',
  flexDirection: 'column',
  gap: spacing[1],
});

const ActionsSection = styled(Box)({
  display: 'flex',
  gap: spacing[2],
  alignItems: 'center',
});

export const PageHeader: React.FC<PageHeaderProps> = ({ title, subtitle, actions }) => {
  return (
    <HeaderContainer>
      <TitleSection>
        <Typography
          variant="h1"
          sx={{
            fontSize: '24px',
            fontWeight: 600,
            color: colors.text.primary,
          }}
        >
          {title}
        </Typography>
        {subtitle && (
          <Typography
            variant="body2"
            sx={{
              fontSize: '14px',
              color: colors.text.secondary,
            }}
          >
            {subtitle}
          </Typography>
        )}
      </TitleSection>
      {actions && <ActionsSection>{actions}</ActionsSection>}
    </HeaderContainer>
  );
};
