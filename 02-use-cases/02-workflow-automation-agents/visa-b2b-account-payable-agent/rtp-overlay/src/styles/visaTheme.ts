/**
 * Visa Payment Theme
 * Professional financial services color palette based on Visa brand guidelines
 * Migrated from Oil & Gas theme to Visa Merchant Portal theme
 */

export const oilGasTheme = {
  colors: {
    // Visa Brand Colors
    visaBlue: '#1A1F71',        // Primary brand color - Visa Blue
    visaBlueLighter: '#2A2F81',  // Lighter variant for hover states
    visaBlueDarker: '#0A0F61',   // Darker variant for active states
    
    visaGold: '#FFC700',         // Accent color - Visa Gold
    visaGoldLight: '#FFD633',    // Lighter gold for hover
    visaGoldDark: '#E6B300',     // Darker gold for active
    
    visaLight: '#F5F6FA',        // Light background - Visa Light
    visaLightHover: '#EBEDF5',   // Slightly darker for hover
    
    // Primary colors (using Visa Blue)
    primary: '#1A1F71',          // Visa Blue - primary actions, headers
    primaryLight: '#2A2F81',     // Lighter - hover states
    primaryDark: '#0A0F61',      // Darker - active states
    
    // Secondary colors (keeping neutral grays)
    secondary: '#2d3748',        // Slate gray - text, borders
    secondaryLight: '#4a5568',   // Medium gray - secondary text
    secondaryDark: '#1a202c',    // Dark gray - footers, dark sections
    
    // Status colors (keeping existing for consistency)
    success: '#48bb78',          // Green - success, pass, matched
    successLight: '#68d391',     // Light green - hover
    successDark: '#38a169',      // Dark green - active
    
    warning: '#f6ad55',          // Orange - warning, pending
    warningLight: '#fbd38d',     // Light orange - hover
    warningDark: '#ed8936',      // Dark orange - active
    
    danger: '#f56565',           // Red - error, fail, mismatch
    dangerLight: '#fc8181',      // Light red - hover
    dangerDark: '#e53e3e',       // Dark red - active
    
    info: '#63b3ed',             // Light blue - info, neutral
    infoLight: '#90cdf4',        // Lighter blue - hover
    infoDark: '#4299e1',         // Darker blue - active
    
    // Background colors
    background: '#F5F6FA',       // Visa Light - page background
    surface: '#ffffff',          // White - card/panel background
    surfaceHover: '#f8f9fa',     // Very light gray - hover state
    
    // Text colors
    text: '#2d3748',             // Dark gray - primary text
    textMuted: '#718096',        // Medium gray - secondary text
    textLight: '#a0aec0',        // Light gray - disabled/placeholder
    
    // Border colors
    border: '#E5E7EB',           // Light gray - default borders
    borderDark: '#cbd5e0',       // Medium gray - emphasized borders
    
    // Special colors
    disputed: '#805ad5',         // Purple - disputed status
    highlight: '#FFF9E6',        // Light yellow - highlighted rows (Visa Gold tint)
    
    // Accent variations
    accent: '#FFC700',           // Visa Gold - special highlights
    accentLight: '#FFD633',      // Light gold
    accentDark: '#E6B300',       // Dark gold
  },
  
  typography: {
    fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', sans-serif",
    fontSize: {
      xs: '1rem',       // 16px - increased for better readability
      sm: '1.125rem',   // 18px - increased for better readability
      base: '1.25rem',  // 20px - increased for better readability
      lg: '1.5rem',     // 24px - increased for better readability
      xl: '1.75rem',    // 28px - increased for better readability
      '2xl': '2.25rem', // 36px - increased for better readability
      '3xl': '2.75rem', // 44px - increased for better readability
      '4xl': '3.5rem',  // 56px - increased for better readability
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
    md: '0.5rem',    // 8px - Visa style (more rounded)
    lg: '0.75rem',   // 12px
    xl: '1rem',      // 16px
    full: '9999px',  // Fully rounded
  },
  
  shadows: {
    sm: '0 1px 2px 0 rgba(26, 31, 113, 0.05)',
    md: '0 4px 6px -1px rgba(26, 31, 113, 0.1), 0 2px 4px -1px rgba(26, 31, 113, 0.06)',
    lg: '0 10px 15px -3px rgba(26, 31, 113, 0.1), 0 4px 6px -2px rgba(26, 31, 113, 0.05)',
    xl: '0 20px 25px -5px rgba(26, 31, 113, 0.1), 0 10px 10px -5px rgba(26, 31, 113, 0.04)',
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
      main: oilGasTheme.colors.visaBlue,
      light: oilGasTheme.colors.visaBlueLighter,
      dark: oilGasTheme.colors.visaBlueDarker,
    },
    secondary: {
      main: oilGasTheme.colors.visaGold,
      light: oilGasTheme.colors.visaGoldLight,
      dark: oilGasTheme.colors.visaGoldDark,
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
    
    // Payment status
    GENERATED: oilGasTheme.colors.visaGold,
    SENT: oilGasTheme.colors.info,
    COMPLETED: oilGasTheme.colors.success,
  };
  
  return statusMap[status] || oilGasTheme.colors.secondary;
};

// Helper for badge styling (Visa style - more rounded)
export const getBadgeStyles = (status: string) => ({
  backgroundColor: getStatusColor(status),
  color: '#ffffff',
  padding: '4px 12px',
  borderRadius: oilGasTheme.borderRadius.md, // More rounded for Visa style
  fontSize: oilGasTheme.typography.fontSize.xs,
  fontWeight: oilGasTheme.typography.fontWeight.semibold,
  textTransform: 'uppercase' as const,
  display: 'inline-block',
});

// Helper for Visa-branded buttons
export const getVisaButtonStyles = (variant: 'primary' | 'secondary' | 'accent' = 'primary') => {
  const variants = {
    primary: {
      backgroundColor: oilGasTheme.colors.visaBlue,
      color: '#ffffff',
      hover: oilGasTheme.colors.visaBlueLighter,
      active: oilGasTheme.colors.visaBlueDarker,
    },
    secondary: {
      backgroundColor: oilGasTheme.colors.surface,
      color: oilGasTheme.colors.visaBlue,
      border: `1px solid ${oilGasTheme.colors.visaBlue}`,
      hover: oilGasTheme.colors.visaLight,
      active: oilGasTheme.colors.visaLightHover,
    },
    accent: {
      backgroundColor: oilGasTheme.colors.visaGold,
      color: oilGasTheme.colors.visaBlue,
      hover: oilGasTheme.colors.visaGoldLight,
      active: oilGasTheme.colors.visaGoldDark,
    },
  };
  
  return variants[variant];
};

export default oilGasTheme;

