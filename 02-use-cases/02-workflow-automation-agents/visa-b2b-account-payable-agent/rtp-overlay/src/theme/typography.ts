import { colors } from './colors';

export const typography = {
  fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  
  // Font Sizes
  fontSize: {
    xs: '12px',
    sm: '14px',
    base: '14px',
    lg: '16px',
    xl: '18px',
    '2xl': '20px',
    '3xl': '24px',
    '4xl': '30px',
  },
  
  // Font Weights
  fontWeight: {
    regular: 400,
    medium: 500,
    semibold: 600,
    bold: 700,
  },
  
  // Line Heights
  lineHeight: {
    tight: 1.25,
    normal: 1.5,
    relaxed: 1.75,
  },
  
  // Text Styles
  styles: {
    h1: {
      fontSize: '24px',
      fontWeight: 600,
      lineHeight: 1.25,
      color: colors.text.primary,
    },
    h2: {
      fontSize: '20px',
      fontWeight: 600,
      lineHeight: 1.25,
      color: colors.text.primary,
    },
    h3: {
      fontSize: '18px',
      fontWeight: 600,
      lineHeight: 1.25,
      color: colors.text.primary,
    },
    body1: {
      fontSize: '14px',
      fontWeight: 400,
      lineHeight: 1.5,
      color: colors.text.primary,
    },
    body2: {
      fontSize: '14px',
      fontWeight: 400,
      lineHeight: 1.5,
      color: colors.text.secondary,
    },
    caption: {
      fontSize: '12px',
      fontWeight: 400,
      lineHeight: 1.5,
      color: colors.text.secondary,
    },
    label: {
      fontSize: '14px',
      fontWeight: 500,
      lineHeight: 1.5,
      color: colors.text.secondary,
    },
  },
};
