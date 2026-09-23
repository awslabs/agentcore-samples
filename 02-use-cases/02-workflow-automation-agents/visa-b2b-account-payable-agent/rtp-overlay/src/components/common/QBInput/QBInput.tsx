import React from 'react';
import { TextField, FormHelperText, Box } from '@mui/material';
import type { TextFieldProps } from '@mui/material';
import { styled } from '@mui/material/styles';
import { colors } from '../../../theme/colors';

interface QBInputProps extends Omit<TextFieldProps, 'variant'> {
  helperText?: string;
  errorText?: string;
}

const StyledTextField = styled(TextField)({
  '& .MuiOutlinedInput-root': {
    borderRadius: '4px',
    '& fieldset': {
      borderColor: colors.border.main,
    },
    '&:hover fieldset': {
      borderColor: colors.border.dark,
    },
    '&.Mui-focused fieldset': {
      borderColor: colors.primary.main,
      borderWidth: '2px',
    },
  },
  '& .MuiInputLabel-root': {
    fontSize: '14px',
    fontWeight: 500,
    color: colors.text.secondary,
    '&.Mui-focused': {
      color: colors.primary.main,
    },
  },
  '& .MuiOutlinedInput-input': {
    padding: '10px 12px',
    fontSize: '14px',
  },
});

const RequiredIndicator = styled('span')({
  color: colors.error.main,
  marginLeft: '4px',
});

export const QBInput: React.FC<QBInputProps> = ({
  label,
  required,
  helperText,
  errorText,
  error,
  ...props
}) => {
  return (
    <Box>
      <StyledTextField
        label={
          <>
            {label}
            {required && <RequiredIndicator>*</RequiredIndicator>}
          </>
        }
        required={required}
        error={error || !!errorText}
        fullWidth
        variant="outlined"
        {...props}
      />
      {helperText && !errorText && (
        <FormHelperText sx={{ color: colors.text.secondary, marginLeft: '14px' }}>
          {helperText}
        </FormHelperText>
      )}
      {errorText && (
        <FormHelperText error sx={{ marginLeft: '14px' }}>
          {errorText}
        </FormHelperText>
      )}
    </Box>
  );
};
