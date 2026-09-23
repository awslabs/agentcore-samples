/**
 * Oil & Gas Industry Theme
 * Professional, muted color palette for enterprise operations
 */

export const oilGasTheme = {
  colors: {
    // Primary colors
    primary: '#004080',      // Navy blue - primary actions, headers
    primaryLight: '#0059b3', // Lighter navy - hover states
    primaryDark: '#003366',  // Darker navy - active states
    
    // Secondary colors
    secondary: '#2d3748',    // Slate gray - text, borders
    secondaryLight: '#4a5568', // Medium gray - secondary text
    secondaryDark: '#1a202c', // Dark gray - footers, dark sections
    
    // Status colors
    success: '#48bb78',      // Green - success, pass, matched
    successLight: '#68d391', // Light green - hover
    successDark: '#38a169',  // Dark green - active
    
    warning: '#f6ad55',      // Orange - warning, pending
    warningLight: '#fbd38d', // Light orange - hover
    warningDark: '#ed8936',  // Dark orange - active
    
    danger: '#f56565',       // Red - error, fail, mismatch
    dangerLight: '#fc8181',  // Light red - hover
    dangerDark: '#e53e3e',   // Dark red - active
    
    info: '#63b3ed',         // Light blue - info, neutral
    infoLight: '#90cdf4',    // Lighter blue - hover
    infoDark: '#4299e1',     // Darker blue - active
    
    // Background colors
    background: '#f7fafc',   // Light gray - page background
    surface: '#ffffff',      // White - card/panel background
    surfaceHover: '#f8f9fa', // Very light gray - hover state
    
    // Text colors
    text: '#2d3748',         // Dark gray - primary text
    textMuted: '#718096',    // Medium gray - secondary text
    textLight: '#a0aec0',    // Light gray - disabled/placeholder
    
    // Border colors
    border: '#e2e8f0',       // Light gray - default borders
    borderDark: '#cbd5e0',   // Medium gray - emphasized borders
    
    // Special colors
    disputed: '#805ad5',     // Purple - disputed status
    highlight: '#fef5e7',    // Light yellow - highlighted rows
  },
  
  typography: {
    fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', sans-serif",
    fontSize: {
      xs: '0.75rem',    // 12px
      sm: '0.875rem',   // 14px
      base: '1rem',     // 16px
      lg: '1.125rem',   // 18px
      xl: '1.25rem',    // 20px
      '2xl': '1.5rem',  // 24px
      '3xl': '1.875rem', // 30px
      '4xl': '2.25rem',  // 36px
    },
    fontWeight: {
      normal: 400,
      medium: 500,
      semibold: 600,
      bold: 700,
    },
    lineHeight: {
      tight: 1.25,
      normal: 1.5,
      relaxed: 1.75,
    },
  },
  
  spacing: {
    xs: '0.25rem',   // 4px
    sm: '0.5rem',    // 8px
    md: '1rem',      // 16px
    lg: '1.5rem',    // 24px
    xl: '2rem',      // 32px
    '2xl': '3rem',   // 48px
    '3xl': '4rem',   // 64px
  },
  
  borderRadius: {
    sm: '0.25rem',   // 4px
    md: '0.5rem',    // 8px
    lg: '0.75rem',   // 12px
    xl: '1rem',      // 16px
    full: '9999px',  // Fully rounded
  },
  
  shadows: {
    sm: '0 1px 2px 0 rgba(0, 0, 0, 0.05)',
    md: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)',
    lg: '0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)',
    xl: '0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)',
  },
  
  transitions: {
    fast: '150ms ease-in-out',
    normal: '250ms ease-in-out',
    slow: '350ms ease-in-out',
  },
};

// Material-UI theme overrides (if using MUI)
export const muiThemeOverrides = {
  palette: {
    primary: {
      main: oilGasTheme.colors.primary,
      light: oilGasTheme.colors.primaryLight,
      dark: oilGasTheme.colors.primaryDark,
    },
    secondary: {
      main: oilGasTheme.colors.secondary,
      light: oilGasTheme.colors.secondaryLight,
      dark: oilGasTheme.colors.secondaryDark,
    },
    success: {
      main: oilGasTheme.colors.success,
      light: oilGasTheme.colors.successLight,
      dark: oilGasTheme.colors.successDark,
    },
    warning: {
      main: oilGasTheme.colors.warning,
      light: oilGasTheme.colors.warningLight,
      dark: oilGasTheme.colors.warningDark,
    },
    error: {
      main: oilGasTheme.colors.danger,
      light: oilGasTheme.colors.dangerLight,
      dark: oilGasTheme.colors.dangerDark,
    },
    info: {
      main: oilGasTheme.colors.info,
      light: oilGasTheme.colors.infoLight,
      dark: oilGasTheme.colors.infoDark,
    },
    background: {
      default: oilGasTheme.colors.background,
      paper: oilGasTheme.colors.surface,
    },
    text: {
      primary: oilGasTheme.colors.text,
      secondary: oilGasTheme.colors.textMuted,
      disabled: oilGasTheme.colors.textLight,
    },
  },
  typography: {
    fontFamily: oilGasTheme.typography.fontFamily,
    fontSize: 16,
  },
  shape: {
    borderRadius: 8,
  },
  shadows: [
    'none',
    oilGasTheme.shadows.sm,
    oilGasTheme.shadows.md,
    oilGasTheme.shadows.lg,
    oilGasTheme.shadows.xl,
    // ... MUI requires 25 shadow levels, repeat as needed
  ],
};

// CSS-in-JS helper for styled-components or emotion
export const getStatusColor = (status: string): string => {
  const statusMap: Record<string, string> = {
    // Match status
    MATCHED: oilGasTheme.colors.success,
    MISMATCHED: oilGasTheme.colors.danger,
    PENDING: oilGasTheme.colors.warning,
    
    // QC status
    PASS: oilGasTheme.colors.success,
    FAIL: oilGasTheme.colors.danger,
    
    // PO status
    OPEN: oilGasTheme.colors.info,
    PARTIALLY_RECEIVED: oilGasTheme.colors.warning,
    FULLY_RECEIVED: oilGasTheme.colors.success,
    CLOSED: oilGasTheme.colors.secondary,
    
    // Invoice status
    APPROVED_FOR_PAYMENT: oilGasTheme.colors.success,
    DISPUTED: oilGasTheme.colors.disputed,
    VOID: oilGasTheme.colors.danger,
  };
  
  return statusMap[status] || oilGasTheme.colors.secondary;
};

// Helper for badge styling
export const getBadgeStyles = (status: string) => ({
  backgroundColor: getStatusColor(status),
  color: '#ffffff',
  padding: '4px 12px',
  borderRadius: oilGasTheme.borderRadius.full,
  fontSize: oilGasTheme.typography.fontSize.xs,
  fontWeight: oilGasTheme.typography.fontWeight.semibold,
  textTransform: 'uppercase' as const,
  display: 'inline-block',
});

export default oilGasTheme;
