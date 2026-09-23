import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Alert, Snackbar } from '@mui/material';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import { PageHeader } from '../layout/PageHeader';
import { QBButton } from '../common/QBButton';
import { useInvoice } from '../../contexts/InvoiceContext';
import { SummaryCards } from './SummaryCards';
import { FilterPanel } from './FilterPanel';
import { InvoiceTable } from './InvoiceTable';
import UploadInvoiceDialog from './UploadInvoiceDialog';
import type { UploadResponse } from '../../types/invoice.types';

export const InvoiceDashboard: React.FC = () => {
  const navigate = useNavigate();
  const {
    invoices,
    loading,
    error,
    filters,
    pagination,
    summary,
    fetchInvoices,
    fetchSummary,
    setFilters,
    clearFilters,
    setPage,
  } = useInvoice();

  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Fetch invoices and summary on mount
  useEffect(() => {
    fetchInvoices();
    fetchSummary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRowClick = (invoiceId: string) => {
    navigate(`/invoices/${invoiceId}`);
  };

  const handlePageChange = (page: number) => {
    setPage(page);
  };

  const handleRowsPerPageChange = (_limit: number) => {
    // Update pagination limit and refetch
    // TODO: Update pagination state with new limit
    fetchInvoices(filters, 1);
  };

  return (
    <Box>
      <PageHeader
        title="Invoice Dashboard"
        subtitle="View and manage all invoices with payment status tracking"
        actions={
          <QBButton
            variant="primary"
            startIcon={<CloudUploadIcon />}
            onClick={() => setUploadDialogOpen(true)}
          >
            Upload Invoice
          </QBButton>
        }
      />

      {/* Error Alert */}
      {error && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => {}}>
          {error}
        </Alert>
      )}

      {/* Summary Cards */}
      <SummaryCards summary={summary} loading={loading} />

      {/* Filter Panel */}
      <FilterPanel
        filters={filters}
        onFilterChange={setFilters}
        onClearFilters={clearFilters}
      />

      {/* Invoice Table */}
      <InvoiceTable
        invoices={invoices}
        loading={loading}
        pagination={pagination}
        onPageChange={handlePageChange}
        onRowsPerPageChange={handleRowsPerPageChange}
        onRowClick={handleRowClick}
      />

      {/* Upload Dialog */}
      <UploadInvoiceDialog
        open={uploadDialogOpen}
        onClose={() => setUploadDialogOpen(false)}
        onUploadSuccess={(response: UploadResponse) => {
          setSuccessMessage(`Invoice "${response.fileName}" uploaded successfully! Processing will begin automatically.`);
          // Refresh the invoice list after a short delay to allow processing
          setTimeout(() => {
            fetchInvoices();
            fetchSummary();
          }, 2000);
        }}
      />

      {/* Success Snackbar */}
      <Snackbar
        open={!!successMessage}
        autoHideDuration={6000}
        onClose={() => setSuccessMessage(null)}
        message={successMessage}
      />
    </Box>
  );
};
