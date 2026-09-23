/**
 * QC Status Badge Component
 * Displays quality control test status
 */

import React from 'react';
import { oilGasTheme, getStatusColor } from '../../styles/oilGasTheme';

export type QCStatus = 'PASS' | 'FAIL';

interface QCStatusBadgeProps {
  status: QCStatus;
  showIcon?: boolean;
  size?: 'sm' | 'md' | 'lg';
}

const QCStatusBadge: React.FC<QCStatusBadgeProps> = ({
  status,
  showIcon = true,
  size = 'md',
}) => {
  const getIcon = () => {
    switch (status) {
      case 'PASS':
        return '✓';
      case 'FAIL':
        return '✗';
      default:
        return '';
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
      <span>{status}</span>
    </span>
  );
};

export default QCStatusBadge;
