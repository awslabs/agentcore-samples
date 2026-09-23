import React, { useEffect } from 'react';
import {
  Box,
  Container,
  Typography,
  CircularProgress,
  Alert,
  Button,
} from '@mui/material';
import { Refresh as RefreshIcon } from '@mui/icons-material';
import { usePayment } from '../../contexts/PaymentContext';
import { PaymentSummaryCards } from './PaymentSummaryCards';
import { PaymentCharts } from './PaymentCharts';
import { PaymentTable } from './PaymentTable';

export const PaymentDashboard: React.FC = () => {
  const {
    payments,
    loading,
    error,
    pagination,
    summary,
    fetchPayments,
    fetchSummary,
    setPage,
    downloadISO20022File,
  } = usePayment();

  useEffect(() => {
    // Fetch payments and summary on mount
    fetchPayments();
    fetchSummary();
  }, []);

  const handleDownloadXML = async (paymentId: string) => {
    try {
      await downloadISO20022File(paymentId);
    } catch (error) {
      console.error('Failed to download XML:', error);
    }
  };

  const handleRefresh = () => {
    fetchPayments();
    fetchSummary();
  };

  const handleRowsPerPageChange = (_limit: number) => {
    // Update pagination limit and refetch
    fetchPayments(undefined, 1);
  };

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      {/* Page Header */}
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          mb: 4,
        }}
      >
        <Box>
          <Typography
            variant="h4"
            sx={{
              fontWeight: 700,
              color: 'text.primary',
              mb: 0.5,
            }}
          >
            Payment Management
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Manage and process invoice payments
          </Typography>
        </Box>
        <Button
          variant="outlined"
          startIcon={<RefreshIcon />}
          onClick={handleRefresh}
          disabled={loading}
        >
          Refresh
        </Button>
      </Box>

      {/* Error State */}
      {error && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => {}}>
          {error}
        </Alert>
      )}

      {/* Summary Cards */}
      <PaymentSummaryCards summary={summary} loading={loading} />

      {/* Charts */}
      <PaymentCharts payments={payments} summary={summary} loading={loading} />

      {/* Payment Table */}
      <Box sx={{ mb: 4 }}>
        <Typography
          variant="h6"
          sx={{ fontWeight: 600, mb: 2, color: 'text.primary' }}
        >
          All Payments
        </Typography>
        {loading && payments.length === 0 ? (
          <Box
            sx={{
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              py: 8,
            }}
          >
            <CircularProgress />
          </Box>
        ) : (
          <PaymentTable
            payments={payments}
            loading={loading}
            pagination={pagination}
            onPageChange={setPage}
            onRowsPerPageChange={handleRowsPerPageChange}
            onDownloadXML={handleDownloadXML}
          />
        )}
      </Box>
    </Container>
  );
};
