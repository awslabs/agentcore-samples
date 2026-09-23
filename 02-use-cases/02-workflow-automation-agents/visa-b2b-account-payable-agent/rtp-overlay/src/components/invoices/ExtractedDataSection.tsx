import React, { useState } from 'react';
import {
  Paper,
  Typography,
  Grid,
  Box,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Divider,
} from '@mui/material';
import { Visibility as VisibilityIcon } from '@mui/icons-material';
import type { ExtractedInvoiceData } from '../../types/invoice.types';
import { formatCurrency, maskAccountNumber } from '../../utils/formatters';

interface ExtractedDataSectionProps {
  data: ExtractedInvoiceData;
  currency: string;
}

export const ExtractedDataSection: React.FC<ExtractedDataSectionProps> = ({
  data,
  currency,
}) => {
  const [showFullAccount, setShowFullAccount] = useState(false);
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false);

  const handleShowFullAccount = () => {
    setConfirmDialogOpen(true);
  };

  const handleConfirmShow = () => {
    setShowFullAccount(true);
    setConfirmDialogOpen(false);
  };

  return (
    <Paper sx={{ p: 3 }}>
      <Typography variant="h6" gutterBottom fontWeight="bold">
        Extracted Invoice Data
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Data automatically extracted from the invoice document using AI
      </Typography>

      {/* Supplier Information */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="subtitle1" fontWeight="bold" gutterBottom>
          Supplier Information
        </Typography>
        <Grid container spacing={2}>
          <Grid size={{ xs: 12, sm: 6 }}>
            <Typography variant="caption" color="text.secondary">
              Name
            </Typography>
            <Typography variant="body2">{data.supplier.name}</Typography>
          </Grid>
          <Grid size={{ xs: 12, sm: 6 }}>
            <Typography variant="caption" color="text.secondary">
              Address
            </Typography>
            <Typography variant="body2">
              {data.supplier.address_lines.join(', ')}
            </Typography>
          </Grid>
          {data.supplier.email && (
            <Grid size={{ xs: 12, sm: 6 }}>
              <Typography variant="caption" color="text.secondary">
                Email
              </Typography>
              <Typography variant="body2">{data.supplier.email}</Typography>
            </Grid>
          )}
          {data.supplier.phone && (
            <Grid size={{ xs: 12, sm: 6 }}>
              <Typography variant="caption" color="text.secondary">
                Phone
              </Typography>
              <Typography variant="body2">{data.supplier.phone}</Typography>
            </Grid>
          )}
        </Grid>
      </Box>

      <Divider sx={{ my: 3 }} />

      {/* Invoice Details */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="subtitle1" fontWeight="bold" gutterBottom>
          Invoice Details
        </Typography>
        <Grid container spacing={2}>
          <Grid size={{ xs: 6, sm: 3 }}>
            <Typography variant="caption" color="text.secondary">
              Invoice Number
            </Typography>
            <Typography variant="body2" fontWeight="medium">
              {data.invoice.number}
            </Typography>
          </Grid>
          <Grid size={{ xs: 6, sm: 3 }}>
            <Typography variant="caption" color="text.secondary">
              Invoice Date
            </Typography>
            <Typography variant="body2">
              {new Date(data.invoice.date).toLocaleDateString()}
            </Typography>
          </Grid>
          <Grid size={{ xs: 6, sm: 3 }}>
            <Typography variant="caption" color="text.secondary">
              Due Date
            </Typography>
            <Typography variant="body2">
              {data.invoice.due_date
                ? new Date(data.invoice.due_date).toLocaleDateString()
                : 'N/A'}
            </Typography>
          </Grid>
          <Grid size={{ xs: 6, sm: 3 }}>
            <Typography variant="caption" color="text.secondary">
              Currency
            </Typography>
            <Typography variant="body2">{data.invoice.currency}</Typography>
          </Grid>
          {data.invoice.po_reference && (
            <Grid size={{ xs: 12, sm: 6 }}>
              <Typography variant="caption" color="text.secondary">
                PO Reference
              </Typography>
              <Typography variant="body2" fontWeight="medium">
                {data.invoice.po_reference}
              </Typography>
            </Grid>
          )}
        </Grid>
      </Box>

      <Divider sx={{ my: 3 }} />

      {/* Line Items */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="subtitle1" fontWeight="bold" gutterBottom>
          Line Items
        </Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Description</TableCell>
                <TableCell align="right">Quantity</TableCell>
                <TableCell align="right">Unit Price</TableCell>
                <TableCell align="right">Amount</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.invoice.line_items.map((item, index) => (
                <TableRow key={index}>
                  <TableCell>{item.description}</TableCell>
                  <TableCell align="right">{item.quantity}</TableCell>
                  <TableCell align="right">
                    {formatCurrency(item.unit_price, currency)}
                  </TableCell>
                  <TableCell align="right">
                    {formatCurrency(item.amount, currency)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Box>

      <Divider sx={{ my: 3 }} />

      {/* Amounts */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="subtitle1" fontWeight="bold" gutterBottom>
          Amounts
        </Typography>
        <Grid container spacing={2}>
          <Grid size={{ xs: 12, sm: 4 }}>
            <Typography variant="caption" color="text.secondary">
              Subtotal
            </Typography>
            <Typography variant="body1">
              {formatCurrency(data.invoice.subtotal, currency)}
            </Typography>
          </Grid>
          <Grid size={{ xs: 12, sm: 4 }}>
            <Typography variant="caption" color="text.secondary">
              Tax Amount
            </Typography>
            <Typography variant="body1">
              {formatCurrency(data.invoice.tax_amount, currency)}
            </Typography>
          </Grid>
          <Grid size={{ xs: 12, sm: 4 }}>
            <Typography variant="caption" color="text.secondary">
              Total
            </Typography>
            <Typography variant="h6" color="primary.main" fontWeight="bold">
              {formatCurrency(data.invoice.total, currency)}
            </Typography>
          </Grid>
        </Grid>
      </Box>

      <Divider sx={{ my: 3 }} />

      {/* Payment Information */}
      <Box>
        <Typography variant="subtitle1" fontWeight="bold" gutterBottom>
          Payment Information
        </Typography>
        <Grid container spacing={2}>
          <Grid size={{ xs: 12, sm: 6 }}>
            <Typography variant="caption" color="text.secondary">
              Bank Name
            </Typography>
            <Typography variant="body2">{data.payment.bank_name}</Typography>
          </Grid>
          <Grid size={{ xs: 12, sm: 6 }}>
            <Typography variant="caption" color="text.secondary">
              Account Name
            </Typography>
            <Typography variant="body2">{data.payment.account_name}</Typography>
          </Grid>
          <Grid size={{ xs: 12, sm: 6 }}>
            <Typography variant="caption" color="text.secondary">
              Account Number
            </Typography>
            <Box display="flex" alignItems="center" gap={1}>
              <Typography variant="body2" fontFamily="monospace">
                {showFullAccount
                  ? data.payment.account_number
                  : maskAccountNumber(data.payment.account_number)}
              </Typography>
              {!showFullAccount && (
                <Button
                  size="small"
                  startIcon={<VisibilityIcon />}
                  onClick={handleShowFullAccount}
                >
                  Show Full
                </Button>
              )}
            </Box>
          </Grid>
          <Grid size={{ xs: 12, sm: 6 }}>
            <Typography variant="caption" color="text.secondary">
              Routing/ABA
            </Typography>
            <Typography variant="body2" fontFamily="monospace">
              {data.payment.routing_aba}
            </Typography>
          </Grid>
          {data.payment.swift_bic && (
            <Grid size={{ xs: 12, sm: 6 }}>
              <Typography variant="caption" color="text.secondary">
                SWIFT/BIC
              </Typography>
              <Typography variant="body2" fontFamily="monospace">
                {data.payment.swift_bic}
              </Typography>
            </Grid>
          )}
          {data.payment.iban && (
            <Grid size={{ xs: 12, sm: 6 }}>
              <Typography variant="caption" color="text.secondary">
                IBAN
              </Typography>
              <Typography variant="body2" fontFamily="monospace">
                {data.payment.iban}
              </Typography>
            </Grid>
          )}
        </Grid>
      </Box>

      {/* Confirmation Dialog */}
      <Dialog open={confirmDialogOpen} onClose={() => setConfirmDialogOpen(false)}>
        <DialogTitle>Show Full Account Number?</DialogTitle>
        <DialogContent>
          <Typography>
            Are you sure you want to view the full account number? This action will be logged for security purposes.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleConfirmShow} variant="contained">
            Show Full Number
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
};
