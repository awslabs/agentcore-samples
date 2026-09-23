/**
 * Match Status Badge Component
 * Displays invoice match validation status
 */

import React from 'react';
import { oilGasTheme, getStatusColor } from '../../styles/oilGasTheme';

export type MatchStatus = 'MATCHED' | 'MISMATCHED' | 'PENDING';

interface MatchStatusBadgeProps {
  status: MatchStatus;
  showIcon?: boolean;
  size?: 'sm' | 'md' | 'lg';
}

const MatchStatusBadge: React.FC<MatchStatusBadgeProps> = ({
  status,
  showIcon = true,
  size = 'md',
}) => {
  const getIcon = () => {
    switch (status) {
      case 'MATCHED':
        return '✓';
      case 'MISMATCHED':
        return '✗';
      case 'PENDING':
        return '⏳';
      default:
        return '';
    }
  };

  const getLabel = () => {
    switch (status) {
      case 'MATCHED':
        return 'Matched';
      case 'MISMATCHED':
        return 'Mismatched';
      case 'PENDING':
        return 'Pending';
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

export default MatchStatusBadge;
