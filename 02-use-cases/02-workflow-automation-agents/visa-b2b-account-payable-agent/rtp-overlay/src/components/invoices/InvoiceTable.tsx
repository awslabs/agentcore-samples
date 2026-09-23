import React from 'react';
import { Box } from '@mui/material';
import { QBTable } from '../common/QBTable';
import type { QBTableColumn } from '../common/QBTable';
import { QBBadge } from '../common/QBBadge';
import type { Invoice, PaginationState, PaymentStatus } from '../../types/invoice.types';
import { CurrencyDisplay } from '../common/CurrencyDisplay';

interface InvoiceTableProps {
  invoices: Invoice[];
  loading: boolean;
  pagination: PaginationState;
  onPageChange: (page: number) => void;
  onRowsPerPageChange: (limit: number) => void;
  onRowClick: (invoiceId: string) => void;
}

export const InvoiceTable: React.FC<InvoiceTableProps> = ({
  invoices,
  loading,
  onRowClick,
}) => {
  const formatDate = (dateString: string): string => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  };

  const getStatusVariant = (status: PaymentStatus): 'success' | 'warning' | 'error' | 'info' | 'default' => {
    switch (status) {
      case 'paid':
        return 'success';
      case 'sent':
      case 'generated':
        return 'info';
      case 'processing':
      case 'pending':
        return 'warning';
      case 'failed':
        return 'error';
      default:
        return 'default';
    }
  };

  const columns: QBTableColumn<Invoice>[] = [
    {
      id: 'invoiceNumber',
      label: 'Invoice #',
      minWidth: 130,
    },
    {
      id: 'vendor',
      label: 'Vendor',
      minWidth: 180,
      format: (value) => value?.name || 'Unknown Vendor',
    },
    {
      id: 'invoiceDate',
      label: 'Invoice Date',
      minWidth: 120,
      format: (value) => formatDate(value),
    },
    {
      id: 'dueDate',
      label: 'Due Date',
      minWidth: 120,
      format: (value) => value ? formatDate(value) : '-',
    },
    {
      id: 'totalAmount',
      label: 'Amount',
      minWidth: 120,
      align: 'right',
      format: (_value, row) => (
        <CurrencyDisplay amount={row.totalAmount} currency={row.currency} />
      ),
    },
    {
      id: 'paymentStatus',
      label: 'Status',
      minWidth: 120,
      format: (value) => (
        <QBBadge
          label={value.toUpperCase()}
          color={getStatusVariant(value)}
        />
      ),
    },
    {
      id: 'matchedPONumber',
      label: 'PO Match',
      minWidth: 120,
      format: (value) => value || '-',
    },
  ];

  if (loading) {
    return <Box>Loading invoices...</Box>;
  }

  return (
    <>
      {invoices.length === 0 ? (
        <Box sx={{ textAlign: 'center', py: 8 }}>
          No invoices found. Try adjusting your filters or check back later.
        </Box>
      ) : (
        <QBTable
          columns={columns}
          data={invoices}
          onRowClick={(row: Invoice) => onRowClick(row.id)}
        />
      )}
    </>
  );
};
