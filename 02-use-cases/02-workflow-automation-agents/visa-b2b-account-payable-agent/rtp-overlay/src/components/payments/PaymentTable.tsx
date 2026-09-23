import React, { useState } from 'react';
import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TablePagination,
  TableSortLabel,
  Paper,
  Button,
  Box,
  Typography,
  Chip,
} from '@mui/material';
import {
  Download as DownloadIcon,
  Warning as WarningIcon,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import type { Payment, PaginationState } from '../../types/payment.types';
import { PaymentStatusChip } from '../common/PaymentStatusChip';

interface PaymentTableProps {
  payments: Payment[];
  loading: boolean;
  pagination: PaginationState;
  onPageChange: (page: number) => void;
  onRowsPerPageChange: (limit: number) => void;
  onDownloadXML: (paymentId: string) => void;
}

type SortField = 'invoiceDate' | 'dueDate' | 'amount';
type SortDirection = 'asc' | 'desc';

export const PaymentTable: React.FC<PaymentTableProps> = ({
  payments,
  loading,
  pagination,
  onPageChange,
  onRowsPerPageChange,
  onDownloadXML,
}) => {
  const navigate = useNavigate();
  const [sortField, setSortField] = useState<SortField>('dueDate');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

  const handleSort = (field: SortField) => {
    const isAsc = sortField === field && sortDirection === 'asc';
    setSortDirection(isAsc ? 'desc' : 'asc');
    setSortField(field);
  };

  const handleRowClick = (paymentId: string) => {
    navigate(`/payments/${paymentId}`);
  };

  const handleChangePage = (_event: unknown, newPage: number) => {
    onPageChange(newPage + 1); // MUI uses 0-based, we use 1-based
  };

  const handleChangeRowsPerPage = (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    onRowsPerPageChange(parseInt(event.target.value, 10));
  };

  const formatCurrency = (amount: number): string => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
    }).format(amount);
  };

  const formatDate = (dateString: string): string => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  };

  const isOverdue = (dueDate: string): boolean => {
    return new Date(dueDate) < new Date();
  };

  // Sort payments
  const sortedPayments = [...payments].sort((a, b) => {
    let aValue: any;
    let bValue: any;

    switch (sortField) {
      case 'invoiceDate':
        aValue = new Date(a.invoiceDate).getTime();
        bValue = new Date(b.invoiceDate).getTime();
        break;
      case 'dueDate':
        aValue = new Date(a.dueDate).getTime();
        bValue = new Date(b.dueDate).getTime();
        break;
      case 'amount':
        aValue = a.amount;
        bValue = b.amount;
        break;
      default:
        return 0;
    }

    if (sortDirection === 'asc') {
      return aValue > bValue ? 1 : -1;
    } else {
      return aValue < bValue ? 1 : -1;
    }
  });

  return (
    <Paper sx={{ width: '100%', overflow: 'hidden' }}>
      <TableContainer sx={{ maxHeight: 600 }}>
        <Table stickyHeader aria-label="Payment list">
          <TableHead>
            <TableRow>
              <TableCell sx={{ fontWeight: 600 }}>Invoice Number</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>Vendor</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>
                <TableSortLabel
                  active={sortField === 'invoiceDate'}
                  direction={sortField === 'invoiceDate' ? sortDirection : 'asc'}
                  onClick={() => handleSort('invoiceDate')}
                >
                  Invoice Date
                </TableSortLabel>
              </TableCell>
              <TableCell sx={{ fontWeight: 600 }}>
                <TableSortLabel
                  active={sortField === 'dueDate'}
                  direction={sortField === 'dueDate' ? sortDirection : 'asc'}
                  onClick={() => handleSort('dueDate')}
                >
                  Due Date
                </TableSortLabel>
              </TableCell>
              <TableCell align="right" sx={{ fontWeight: 600 }}>
                <TableSortLabel
                  active={sortField === 'amount'}
                  direction={sortField === 'amount' ? sortDirection : 'asc'}
                  onClick={() => handleSort('amount')}
                >
                  Amount
                </TableSortLabel>
              </TableCell>
              <TableCell sx={{ fontWeight: 600 }}>Status</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>ISO 20022 File</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {sortedPayments.map((payment) => (
              <TableRow
                key={payment.id}
                hover
                onClick={() => handleRowClick(payment.id)}
                sx={{
                  cursor: 'pointer',
                  '&:hover': {
                    backgroundColor: '#f5f5f5',
                  },
                }}
              >
                <TableCell>
                  <Typography
                    variant="body2"
                    sx={{ fontWeight: 500, color: 'primary.main' }}
                  >
                    {payment.invoiceNumber}
                  </Typography>
                </TableCell>
                <TableCell>
                  <Typography variant="body2">{payment.vendor.name}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {payment.vendor.code}
                  </Typography>
                </TableCell>
                <TableCell>
                  <Typography variant="body2">
                    {formatDate(payment.invoiceDate)}
                  </Typography>
                </TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    {isOverdue(payment.dueDate) && (
                      <WarningIcon
                        sx={{ fontSize: 18, color: 'error.main' }}
                      />
                    )}
                    <Typography
                      variant="body2"
                      sx={{
                        color: isOverdue(payment.dueDate)
                          ? 'error.main'
                          : 'text.primary',
                        fontWeight: isOverdue(payment.dueDate) ? 600 : 400,
                      }}
                    >
                      {formatDate(payment.dueDate)}
                    </Typography>
                  </Box>
                </TableCell>
                <TableCell align="right">
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {formatCurrency(payment.amount)}
                  </Typography>
                </TableCell>
                <TableCell>
                  <PaymentStatusChip status={payment.paymentStatus} size="small" />
                </TableCell>
                <TableCell>
                  {payment.iso20022FileKey ? (
                    <Button
                      size="small"
                      startIcon={<DownloadIcon />}
                      onClick={(e) => {
                        e.stopPropagation();
                        onDownloadXML(payment.id);
                      }}
                      sx={{ textTransform: 'none' }}
                    >
                      Download XML
                    </Button>
                  ) : (
                    <Chip
                      label="Not Generated"
                      size="small"
                      sx={{
                        backgroundColor: '#f5f5f5',
                        color: '#666',
                      }}
                    />
                  )}
                </TableCell>
              </TableRow>
            ))}
            {sortedPayments.length === 0 && !loading && (
              <TableRow>
                <TableCell colSpan={7} align="center" sx={{ py: 4 }}>
                  <Typography variant="body1" color="text.secondary">
                    No payments found
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>
      <TablePagination
        rowsPerPageOptions={[10, 25, 50, 100]}
        component="div"
        count={pagination.total}
        rowsPerPage={pagination.limit}
        page={pagination.page - 1} // MUI uses 0-based
        onPageChange={handleChangePage}
        onRowsPerPageChange={handleChangeRowsPerPage}
      />
    </Paper>
  );
};
