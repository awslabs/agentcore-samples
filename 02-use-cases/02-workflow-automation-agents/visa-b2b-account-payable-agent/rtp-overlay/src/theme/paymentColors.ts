// Payment Status Color Scheme

export const paymentStatusColors = {
  ready: {
    main: '#2196F3', // Blue
    light: '#E3F2FD',
    dark: '#1976D2',
  },
  scheduled: {
    main: '#9C27B0', // Purple
    light: '#F3E5F5',
    dark: '#7B1FA2',
  },
  processing: {
    main: '#FF9800', // Orange
    light: '#FFF3E0',
    dark: '#F57C00',
  },
  sent: {
    main: '#4CAF50', // Green
    light: '#E8F5E9',
    dark: '#388E3C',
  },
  paid: {
    main: '#4CAF50', // Success Green
    light: '#E8F5E9',
    dark: '#2E7D32',
  },
  failed: {
    main: '#F44336', // Red
    light: '#FFEBEE',
    dark: '#D32F2F',
  },
  cancelled: {
    main: '#9E9E9E', // Gray
    light: '#F5F5F5',
    dark: '#616161',
  },
};

export type PaymentStatusColorKey = keyof typeof paymentStatusColors;
