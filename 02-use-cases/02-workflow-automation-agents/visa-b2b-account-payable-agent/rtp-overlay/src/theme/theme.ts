import { createTheme } from '@mui/material/styles';
import { colors } from './colors';
import { typography } from './typography';
import { shadows } from './shadows';

export const qbTheme = createTheme({
  palette: {
    primary: {
      main: colors.primary.main,
      light: colors.primary.light,
      dark: colors.primary.dark,
    },
    secondary: {
      main: colors.gray[500],
      light: colors.gray[300],
      dark: colors.gray[700],
    },
    success: {
      main: colors.success.main,
      light: colors.success.light,
      dark: colors.success.dark,
    },
    error: {
      main: colors.error.main,
      light: colors.error.light,
      dark: colors.error.dark,
    },
    warning: {
      main: colors.warning.main,
      light: colors.warning.light,
      dark: colors.warning.dark,
    },
    info: {
      main: colors.info.main,
      light: colors.info.light,
      dark: colors.info.dark,
    },
    background: {
      default: colors.background.default,
      paper: colors.background.paper,
    },
    text: {
      primary: colors.text.primary,
      secondary: colors.text.secondary,
      disabled: colors.text.disabled,
    },
  },
  typography: {
    fontFamily: typography.fontFamily,
    h1: {
      fontSize: typography.styles.h1.fontSize,
      fontWeight: typography.styles.h1.fontWeight,
      lineHeight: typography.styles.h1.lineHeight,
      color: typography.styles.h1.color,
    },
    h2: {
      fontSize: typography.styles.h2.fontSize,
      fontWeight: typography.styles.h2.fontWeight,
      lineHeight: typography.styles.h2.lineHeight,
      color: typography.styles.h2.color,
    },
    h3: {
      fontSize: typography.styles.h3.fontSize,
      fontWeight: typography.styles.h3.fontWeight,
      lineHeight: typography.styles.h3.lineHeight,
      color: typography.styles.h3.color,
    },
    body1: {
      fontSize: typography.styles.body1.fontSize,
      fontWeight: typography.styles.body1.fontWeight,
      lineHeight: typography.styles.body1.lineHeight,
      color: typography.styles.body1.color,
    },
    body2: {
      fontSize: typography.styles.body2.fontSize,
      fontWeight: typography.styles.body2.fontWeight,
      lineHeight: typography.styles.body2.lineHeight,
      color: typography.styles.body2.color,
    },
    caption: {
      fontSize: typography.styles.caption.fontSize,
      fontWeight: typography.styles.caption.fontWeight,
      lineHeight: typography.styles.caption.lineHeight,
      color: typography.styles.caption.color,
    },
  },
  shadows: [
    'none',
    shadows.sm,
    shadows.base,
    shadows.md,
    shadows.md,
    shadows.lg,
    shadows.lg,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
    shadows.xl,
  ],
  shape: {
    borderRadius: 4,
  },
  spacing: 8,
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          fontWeight: typography.fontWeight.medium,
          borderRadius: '4px',
          padding: '8px 16px',
        },
        contained: {
          boxShadow: shadows.sm,
          '&:hover': {
            boxShadow: shadows.base,
          },
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          boxShadow: shadows.sm,
          borderRadius: '8px',
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
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
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        head: {
          fontWeight: typography.fontWeight.semibold,
          backgroundColor: colors.gray[100],
          color: colors.text.primary,
        },
      },
    },
  },
});
