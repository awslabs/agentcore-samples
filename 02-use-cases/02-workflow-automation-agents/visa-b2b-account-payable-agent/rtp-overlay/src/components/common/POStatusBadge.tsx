/**
 * PO Status Badge Component
 * Displays purchase order status
 */

import React from 'react';
import { oilGasTheme, getStatusColor } from '../../styles/oilGasTheme';

export type POStatus = 'OPEN' | 'PARTIALLY_RECEIVED' | 'FULLY_RECEIVED' | 'CLOSED';

interface POStatusBadgeProps {
  status: POStatus;
  showIcon?: boolean;
  size?: 'sm' | 'md' | 'lg';
}

const POStatusBadge: React.FC<POStatusBadgeProps> = ({
  status,
  showIcon = true,
  size = 'md',
}) => {
  const getIcon = () => {
    switch (status) {
      case 'OPEN':
        return '○';
      case 'PARTIALLY_RECEIVED':
        return '◐';
      case 'FULLY_RECEIVED':
        return '●';
      case 'CLOSED':
        return '✓';
      default:
        return '';
    }
  };

  const getLabel = () => {
    switch (status) {
      case 'OPEN':
        return 'Open';
      case 'PARTIALLY_RECEIVED':
        return 'Partial';
      case 'FULLY_RECEIVED':
        return 'Received';
      case 'CLOSED':
        return 'Closed';
      default:
        return status;
    }
  };

  const sizeStyles = {
    sm: {
      padding: '2px 8px',
      fontSize: oilGasTheme.typography.fontSize.xs,
    },
    md: {
      padding: '4px 12px',
      fontSize: oilGasTheme.typography.fontSize.sm,
    },
    lg: {
      padding: '6px 16px',
      fontSize: oilGasTheme.typography.fontSize.base,
    },
  };

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        backgroundColor: getStatusColor(status),
        color: '#ffffff',
        ...sizeStyles[size],
        borderRadius: oilGasTheme.borderRadius.full,
        fontWeight: oilGasTheme.typography.fontWeight.semibold,
        textTransform: 'uppercase',
        whiteSpace: 'nowrap',
      }}
    >
      {showIcon && <span>{getIcon()}</span>}
      <span>{getLabel()}</span>
    </span>
  );
};

export default POStatusBadge;
