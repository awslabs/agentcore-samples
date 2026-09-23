import React from 'react';
import { Button as MuiButton } from '@mui/material';
import type { ButtonProps as MuiButtonProps } from '@mui/material';

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'text';

interface CustomButtonProps extends Omit<MuiButtonProps, 'variant' | 'color'> {
  variant?: ButtonVariant;
}

export const Button: React.FC<CustomButtonProps> = ({ variant = 'primary', ...props }) => {
  const getButtonProps = (): Partial<MuiButtonProps> => {
    switch (variant) {
      case 'primary':
        return { variant: 'contained', color: 'primary' };
      case 'secondary':
        return { variant: 'outlined', color: 'primary' };
      case 'danger':
        return { variant: 'contained', color: 'error' };
      case 'text':
        return { variant: 'text', color: 'primary' };
      default:
        return { variant: 'contained', color: 'primary' };
    }
  };

  return <MuiButton {...getButtonProps()} {...props} />;
};
