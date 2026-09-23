import React from 'react';
import { Card as MuiCard, CardContent, CardHeader, CardActions } from '@mui/material';

interface CardProps {
  title?: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
  elevation?: number;
}

export const Card: React.FC<CardProps> = ({ title, children, actions, elevation = 1 }) => {
  return (
    <MuiCard elevation={elevation}>
      {title && <CardHeader title={title} />}
      <CardContent>{children}</CardContent>
      {actions && <CardActions>{actions}</CardActions>}
    </MuiCard>
  );
};
