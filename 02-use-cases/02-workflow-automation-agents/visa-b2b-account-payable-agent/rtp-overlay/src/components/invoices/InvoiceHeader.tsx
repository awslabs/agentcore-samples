import React from 'react';
import {
  Box,
  Paper,
  Typography,
  Chip,
  Button,
  Grid,
  Alert,
  Divider,
} from '@mui/material';
import {
  Download as DownloadIcon,
  Link as LinkIcon,
  Edit as EditIcon,
  Description as DescriptionIcon,
  Warning as WarningIcon,
} from '@mui/icons-material';
import type { Invoice } from '../../types/invoice.types';
import { formatCurrency } from '../../utils/formatters';

interface InvoiceHeaderProps {
  invoice: Invoice;
  onDownloadPDF: () => void;
  onDownloadXML: () => void;
  onLinkPO: () => void;
  onUpdateStatus: () => void;
  downloading: boolean;
}

const getStatusColor = (status: string) => {
  const colors: Record<string, 'default' | 'primary' | 'secondary' | 'error' | 'warning' | 'info' | 'success'> = {
    pending: 'warning',
    processing: 'info',
    generated: 'primary',
    sent: 'secondary',
    paid: 'success',
    failed: 'error',
  };
  return colors[status] || 'default';
};

export const InvoiceHeader: React.FC<InvoiceHeaderProps> = ({
  invoice,
  onDownloadPDF,
  onDownloadXML,
  onLinkPO,
  onUpdateStatus,
  downloading,
}) => {
  return (
    <Paper sx={{ p: 3, mb: 3 }}>
      {/* Variance Warning Banner */}
      {invoice.hasVarianceWarning && (
        <Alert severity="warning" icon={<WarningIcon />} sx={{ mb: 2 }}>
          <Typography variant="body2" fontWeight="medium">
            Variance Warning: This invoice has a {invoice.variancePercentage?.toFixed(1)}% variance from the matched purchase order.
          </Typography>
        </Alert>
      )}

      {/* No PO Match Warning */}
      {!invoice.hasPoMatch && (
        <Alert severity="info" sx={{ mb: 2 }}>
          <Typography variant="body2" fontWeight="medium">
            No Purchase Order Match: This invoice has not been linked to a purchase order yet.
          </Typography>
        </Alert>
      )}

      {/* Header Content */}
      <Grid container spacing={3}>
        {/* Left Section - Invoice Info */}
        <Grid size={{ xs: 12, md: 8 }}>
          <Box display="flex" alignItems="center" gap={2} mb={2}>
            <DescriptionIcon sx={{ fontSize: 40, color: 'primary.main' }} />
            <Box>
              <Typography variant="h5" fontWeight="bold">
                Invoice {invoice.invoiceNumber}
              </Typography>
              <Box display="flex" alignItems="center" gap={1} mt={0.5}>
                <Chip
                  label={invoice.paymentStatus.toUpperCase()}
                  color={getStatusColor(invoice.paymentStatus)}
                  size="small"
                />
                {invoice.hasPoMatch && (
                  <Chip
                    label="PO Matched"
                    color="success"
                    size="small"
                    variant="outlined"
                  />
                )}
              </Box>
            </Box>
          </Box>

          <Divider sx={{ my: 2 }} />

          <Grid container spacing={2}>
            <Grid size={{ xs: 6, sm: 3 }}>
              <Typography variant="caption" color="text.secondary">
                Vendor
              </Typography>
              <Typography variant="body2" fontWeight="medium">
                {invoice.vendor?.name || invoice.extractedData.supplier.name}
              </Typography>
            </Grid>
            <Grid size={{ xs: 6, sm: 3 }}>
              <Typography variant="caption" color="text.secondary">
                Invoice Date
              </Typography>
              <Typography variant="body2" fontWeight="medium">
                {new Date(invoice.invoiceDate).toLocaleDateString()}
              </Typography>
            </Grid>
            <Grid size={{ xs: 6, sm: 3 }}>
              <Typography variant="caption" color="text.secondary">
                Due Date
              </Typography>
              <Typography variant="body2" fontWeight="medium">
                {invoice.dueDate
                  ? new Date(invoice.dueDate).toLocaleDateString()
                  : 'N/A'}
              </Typography>
            </Grid>
            <Grid size={{ xs: 6, sm: 3 }}>
              <Typography variant="caption" color="text.secondary">
                Total Amount
              </Typography>
              <Typography variant="h6" color="primary.main" fontWeight="bold">
                {formatCurrency(invoice.totalAmount, invoice.currency)}
              </Typography>
            </Grid>
          </Grid>
        </Grid>

        {/* Right Section - Actions */}
        <Grid size={{ xs: 12, md: 4 }}>
          <Box display="flex" flexDirection="column" gap={1.5}>
            <Button
              variant="contained"
              startIcon={<DownloadIcon />}
              onClick={onDownloadPDF}
              disabled={downloading}
              fullWidth
            >
              Download Invoice PDF
            </Button>
            <Button
              variant="outlined"
              startIcon={<DownloadIcon />}
              onClick={onDownloadXML}
              disabled={!invoice.iso20022FileKey || downloading}
              fullWidth
            >
              Download ISO 20022 XML
            </Button>
            <Button
              variant="outlined"
              startIcon={<LinkIcon />}
              onClick={onLinkPO}
              disabled={invoice.hasPoMatch}
              fullWidth
            >
              {invoice.hasPoMatch ? 'PO Linked' : 'Link to PO'}
            </Button>
            <Button
              variant="outlined"
              startIcon={<EditIcon />}
              onClick={onUpdateStatus}
              fullWidth
            >
              Update Status
            </Button>
          </Box>
        </Grid>
      </Grid>
    </Paper>
  );
};
