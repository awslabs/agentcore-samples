import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  Typography,
  Paper,
  Alert,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  CircularProgress,
} from '@mui/material';
import { ArrowBack, CheckCircle, Warning, Error as ErrorIcon, Payment as PaymentIcon } from '@mui/icons-material';
import { oilGasTheme } from '../../styles/oilGasTheme';
import MatchStatusBadge from '../../components/common/MatchStatusBadge';
import { VirtualCardDisplay } from '../../components/payments/VirtualCardDisplay';
import { PaymentStatusBadge } from '../../components/payments/PaymentStatusBadge';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'https://56hm3anw06.execute-api.us-east-1.amazonaws.com/prod';

interface InvoiceData {
  id: string;
  invoiceNumber: string;
  vendorName: string;
  totalAmount: number;
  invoiceDate: string;
  dueDate?: string;
  matchStatus: 'MATCHED' | 'MISMATCHED' | 'PENDING';
  validationFlags: string[];
  lineItems: Array<{
    description: string;
    quantity: number;
    unitPrice: number;
  }>;
  purchaseOrder?: any;
  goodsReceipt?: any;
  qcRecord?: any;
  iso20022FileKey?: string;
  // 3-way match fields
  purchaseOrderId?: string;
  goodsReceiptId?: string;
  hasPoMatch?: boolean;
  variancePercentage?: number;
  hasVarianceWarning?: boolean;
}

export const InvoiceMatchDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [invoice, setInvoice] = useState<InvoiceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Payment processing state
  const [processingPayment, setProcessingPayment] = useState(false);
  const [paymentResult, setPaymentResult] = useState<any>(null);
  const [paymentError, setPaymentError] = useState<string | null>(null);

  useEffect(() => {
    fetchInvoice();
  }, [id]);

  const fetchInvoice = async () => {
    try {
      setLoading(true);
      const token = localStorage.getItem('accessToken');
      const headers: HeadersInit = {
        'Content-Type': 'application/json',
      };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const response = await fetch(`${API_BASE_URL}/api/invoices/${id}`, {
        headers,
      });

      if (!response.ok) {
        if (response.status === 404) {
          setError('Invoice not found');
          return;
        }
        throw new Error(`Failed to fetch invoice: ${response.statusText}`);
      }

      const inv = await response.json();

      // Transform backend data
      const transformedInvoice: InvoiceData = {
        id: inv.id,
        invoiceNumber: inv.invoiceNumber,
        vendorName: inv.vendor?.name || 'Unknown Vendor',
        totalAmount: parseFloat(inv.totalAmount),
        invoiceDate: inv.invoiceDate,
        matchStatus: inv.matchStatus || (inv.hasPoMatch ? 'MATCHED' : inv.hasVarianceWarning ? 'MISMATCHED' : 'PENDING'),
        validationFlags: inv.validationFlags || [],
        lineItems: inv.extractedData?.invoice?.line_items || [],
        purchaseOrder: inv.purchaseOrder,
        goodsReceipt: inv.goodsReceipt,
        qcRecord: null,
        iso20022FileKey: inv.iso20022FileKey,
        // 3-way match fields
        purchaseOrderId: inv.purchaseOrderId,
        goodsReceiptId: inv.goodsReceiptId,
        hasPoMatch: inv.hasPoMatch,
        variancePercentage: inv.variancePercentage,
        hasVarianceWarning: inv.hasVarianceWarning,
      };

      setInvoice(transformedInvoice);
      setError(null);
    } catch (err: unknown) {
      console.error('Error fetching invoice:', err);
      setError(err instanceof Error ? err.message : 'Failed to load invoice');
    } finally {
      setLoading(false);
    }
  };

  const handleProcessPayment = async () => {
    if (!invoice) return;

    try {
      setProcessingPayment(true);
      setPaymentError(null);

      const token = localStorage.getItem('accessToken');
      const headers: HeadersInit = {
        'Content-Type': 'application/json',
      };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const response = await fetch(`${API_BASE_URL}/api/payments/process`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ invoice_id: invoice.id }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.details || errorData.error || 'Failed to process payment');
      }

      const result = await response.json();
      console.log('Payment processed:', result);

      // Fetch card details if payment was successful
      // Backend returns payment.id, not payment.payment_id
      const paymentId = result.payment?.id || result.payment?.payment_id;
      
      if (paymentId && result.payment?.payment_method === 'visa_b2b') {
        console.log('Fetching card details for payment:', paymentId);
        const cardResponse = await fetch(
          `${API_BASE_URL}/api/payments/${paymentId}/card-details`,
          { headers }
        );

        if (cardResponse.ok) {
          const cardDetails = await cardResponse.json();
          console.log('Card details fetched:', cardDetails);
          setPaymentResult({
            ...result.payment,
            cardDetails,
          });
        } else {
          console.error('Failed to fetch card details:', cardResponse.status);
          setPaymentResult(result.payment);
        }
      } else {
        console.log('No card details to fetch (payment method:', result.payment?.payment_method, ')');
        setPaymentResult(result.payment);
      }

      // Refresh invoice data to get updated iso20022FileKey
      await fetchInvoice();
    } catch (err: unknown) {
      console.error('Error processing payment:', err);
      setPaymentError(err instanceof Error ? err.message : 'Failed to process payment');
    } finally {
      setProcessingPayment(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ p: 3, display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '400px' }}>
        <CircularProgress />
      </Box>
    );
  }

  if (error || !invoice) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error">{error || 'Invoice not found'}</Alert>
        <Button onClick={() => navigate('/invoices')} sx={{ mt: 2 }}>
          Back to List
        </Button>
      </Box>
    );
  }

  const purchaseOrder = invoice.purchaseOrder;
  const goodsReceipt = invoice.goodsReceipt;
  const qcRecord = invoice.qcRecord;

  const formatCurrency = (amount: number) => {
    return amount.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
  };

  // Calculate totals for variance calculations
  const totalInvoiceQty = invoice.lineItems?.reduce((sum, item) => sum + (item.quantity || 0), 0) || 0;
  const totalPoQty = purchaseOrder?.items?.reduce((sum: number, item: any) => sum + (Number(item.quantity) || 0), 0) || 0;
  
  // Calculate variances using totals
  const quantityVariance = purchaseOrder && goodsReceipt && totalInvoiceQty && goodsReceipt.quantityReceived
    ? ((totalInvoiceQty - goodsReceipt.quantityReceived) / goodsReceipt.quantityReceived) * 100
    : 0;

  const priceVariance = purchaseOrder && invoice.totalAmount && purchaseOrder.totalAmount
    ? ((invoice.totalAmount - purchaseOrder.totalAmount) / purchaseOrder.totalAmount) * 100
    : 0;

  return (
    <Box sx={{ p: 3 }}>
      {/* Header */}
      <Box sx={{ mb: 3, display: 'flex', alignItems: 'center', gap: 2 }}>
        <Button
          startIcon={<ArrowBack />}
          onClick={() => navigate('/invoices')}
          sx={{ color: oilGasTheme.colors.textMuted }}
        >
          Back
        </Button>
        <Box sx={{ flex: 1 }}>
          <Typography variant="h4" sx={{ color: oilGasTheme.colors.primary }}>
            Invoice Details
          </Typography>
          <Typography variant="body2" color="textSecondary">
            {invoice.invoiceNumber}
          </Typography>
        </Box>
        <MatchStatusBadge status={invoice.matchStatus} />
      </Box>

      {/* Invoice Details Panel */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 3 }}>
          Invoice Information
        </Typography>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr 1fr' }, gap: 3, mb: 3 }}>
          <Box>
            <Typography variant="body2" color="textSecondary" sx={{ fontSize: '0.875rem' }}>
              Invoice Number
            </Typography>
            <Typography variant="h6" fontWeight={600} sx={{ fontSize: '1.125rem' }}>
              {invoice.invoiceNumber}
            </Typography>
          </Box>
          <Box>
            <Typography variant="body2" color="textSecondary" sx={{ fontSize: '0.875rem' }}>
              Invoice Date
            </Typography>
            <Typography variant="h6" sx={{ fontSize: '1.125rem' }}>
              {new Date(invoice.invoiceDate).toLocaleDateString()}
            </Typography>
          </Box>
          <Box>
            <Typography variant="body2" color="textSecondary" sx={{ fontSize: '0.875rem' }}>
              Due Date
            </Typography>
            <Typography variant="h6" sx={{ fontSize: '1.125rem' }}>
              {invoice.dueDate ? new Date(invoice.dueDate).toLocaleDateString() : 'Not specified'}
            </Typography>
          </Box>
          <Box>
            <Typography variant="body2" color="textSecondary" sx={{ fontSize: '0.875rem' }}>
              Vendor
            </Typography>
            <Typography variant="h6" fontWeight={600} sx={{ fontSize: '1.125rem' }}>
              {invoice.vendorName}
            </Typography>
          </Box>
          <Box>
            <Typography variant="body2" color="textSecondary" sx={{ fontSize: '0.875rem' }}>
              Currency
            </Typography>
            <Typography variant="h6" sx={{ fontSize: '1.125rem' }}>
              USD
            </Typography>
          </Box>
          <Box>
            <Typography variant="body2" color="textSecondary" sx={{ fontSize: '0.875rem' }}>
              Match Status
            </Typography>
            <Box sx={{ mt: 0.5 }}>
              <MatchStatusBadge status={invoice.matchStatus} />
            </Box>
          </Box>
        </Box>

        {/* Amount Breakdown */}
        <Box sx={{ 
          borderTop: '1px solid', 
          borderColor: 'divider', 
          pt: 2,
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', md: '2fr 1fr' },
          gap: 3
        }}>
          {/* Line Items */}
          <Box>
            <Typography variant="subtitle1" color="textSecondary" gutterBottom sx={{ fontSize: '1rem', fontWeight: 600 }}>
              Line Items
            </Typography>
            {invoice.lineItems && invoice.lineItems.length > 0 ? (
              <Box sx={{ mt: 1 }}>
                {invoice.lineItems.map((item: any, index: number) => {
                  // Calculate unit price and amount correctly
                  const quantity = item.quantity || 0;
                  const unitPrice = item.unit_price || item.unitPrice || 0;
                  const amount = item.amount || (quantity * unitPrice);
                  
                  return (
                    <Box key={index} sx={{ mb: 1.5 }}>
                      <Typography variant="body1" fontWeight={600} sx={{ fontSize: '1rem' }}>
                        {item.description || 'No description'}
                      </Typography>
                      <Typography variant="body2" color="textSecondary" sx={{ fontSize: '0.9rem' }}>
                        Qty: {quantity} × {formatCurrency(unitPrice)} = {formatCurrency(amount)}
                      </Typography>
                    </Box>
                  );
                })}
              </Box>
            ) : (
              <Typography variant="body1" color="textSecondary" sx={{ fontSize: '1rem' }}>
                No line items available
              </Typography>
            )}
          </Box>

          {/* Amount Summary */}
          <Box>
            <Typography variant="subtitle1" color="textSecondary" gutterBottom sx={{ fontSize: '1rem', fontWeight: 600 }}>
              Amount Summary
            </Typography>
            <Box sx={{ mt: 1 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
                <Typography variant="body1" sx={{ fontSize: '1rem' }}>Subtotal:</Typography>
                <Typography variant="body1" sx={{ fontSize: '1rem' }}>{formatCurrency(invoice.totalAmount)}</Typography>
              </Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
                <Typography variant="body1" sx={{ fontSize: '1rem' }}>Tax:</Typography>
                <Typography variant="body1" sx={{ fontSize: '1rem' }}>$0.00</Typography>
              </Box>
              <Box sx={{ 
                display: 'flex', 
                justifyContent: 'space-between', 
                pt: 1, 
                borderTop: '1px solid',
                borderColor: 'divider'
              }}>
                <Typography variant="h6" fontWeight={700} sx={{ fontSize: '1.125rem' }}>Total:</Typography>
                <Typography variant="h5" sx={{ color: oilGasTheme.colors.primary, fontWeight: 700, fontSize: '1.5rem' }}>
                  {formatCurrency(invoice.totalAmount)}
                </Typography>
              </Box>
            </Box>
          </Box>
        </Box>
      </Paper>

      {/* 4-Way Match Section Header */}
      <Typography variant="h5" sx={{ mb: 2, color: oilGasTheme.colors.primary }}>
        4-Way Match Validation
      </Typography>

      {/* 3-Way Match Status Cards */}
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2, mb: 3 }}>
        {/* Purchase Order Match */}
        <Paper sx={{ 
          p: 2, 
          border: '2px solid',
          borderColor: invoice.hasPoMatch && invoice.purchaseOrderId ? oilGasTheme.colors.success : oilGasTheme.colors.danger,
          backgroundColor: invoice.hasPoMatch && invoice.purchaseOrderId ? oilGasTheme.colors.success + '10' : oilGasTheme.colors.danger + '10'
        }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            {invoice.hasPoMatch && invoice.purchaseOrderId ? (
              <CheckCircle sx={{ color: oilGasTheme.colors.success, fontSize: 32 }} />
            ) : (
              <ErrorIcon sx={{ color: oilGasTheme.colors.danger, fontSize: 32 }} />
            )}
            <Box>
              <Typography variant="h6" fontWeight={600}>
                Purchase Order
              </Typography>
              <Typography variant="body2" color="textSecondary">
                {invoice.purchaseOrderId ? `PO ID: ${invoice.purchaseOrderId}` : 'No PO matched'}
              </Typography>
            </Box>
          </Box>
        </Paper>

        {/* Goods Receipt Match */}
        <Paper sx={{ 
          p: 2, 
          border: '2px solid',
          borderColor: invoice.goodsReceiptId ? oilGasTheme.colors.success : oilGasTheme.colors.danger,
          backgroundColor: invoice.goodsReceiptId ? oilGasTheme.colors.success + '10' : oilGasTheme.colors.danger + '10'
        }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            {invoice.goodsReceiptId ? (
              <CheckCircle sx={{ color: oilGasTheme.colors.success, fontSize: 32 }} />
            ) : (
              <ErrorIcon sx={{ color: oilGasTheme.colors.danger, fontSize: 32 }} />
            )}
            <Box>
              <Typography variant="h6" fontWeight={600}>
                Goods Receipt
              </Typography>
              <Typography variant="body2" color="textSecondary">
                {invoice.goodsReceiptId ? `GR ID: ${invoice.goodsReceiptId}` : 'No GR matched'}
              </Typography>
            </Box>
          </Box>
        </Paper>
      </Box>

      {/* Match Status Alert */}
      <Alert
        severity={
          invoice.matchStatus === 'MATCHED'
            ? 'success'
            : invoice.matchStatus === 'PENDING'
            ? 'warning'
            : 'error'
        }
        sx={{ mb: 3 }}
      >
        <Typography variant="h6">
          Overall Match Status: <strong>{invoice.matchStatus}</strong>
        </Typography>
        {invoice.variancePercentage !== undefined && invoice.variancePercentage !== null && invoice.variancePercentage !== 0 && (
          <Typography variant="body2" sx={{ mt: 1 }}>
            Amount Variance: {Number(invoice.variancePercentage) > 0 ? '+' : ''}{Number(invoice.variancePercentage).toFixed(2)}%
            {invoice.hasVarianceWarning && ' (Exceeds 10% tolerance)'}
          </Typography>
        )}
        {invoice.validationFlags.length > 0 && (
          <Box sx={{ mt: 1, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            {invoice.validationFlags.map((flag, index) => (
              <Chip
                key={index}
                label={flag.replace('_', ' ').toUpperCase()}
                size="small"
                color={invoice.matchStatus === 'MATCHED' ? 'success' : 'error'}
              />
            ))}
          </Box>
        )}
      </Alert>

      {/* 4-Way Comparison Table */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Document Comparison
        </Typography>
        <TableContainer>
          <Table>
            <TableHead>
              <TableRow sx={{ backgroundColor: oilGasTheme.colors.background }}>
                <TableCell>
                  <strong>Field</strong>
                </TableCell>
                <TableCell>
                  <strong>Purchase Order</strong>
                </TableCell>
                <TableCell>
                  <strong>Goods Receipt</strong>
                </TableCell>
                <TableCell>
                  <strong>Quality Control</strong>
                </TableCell>
                <TableCell>
                  <strong>Invoice</strong>
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {/* Vendor */}
              <TableRow>
                <TableCell>
                  <strong>Vendor</strong>
                </TableCell>
                <TableCell>{purchaseOrder?.vendorName || 'N/A'}</TableCell>
                <TableCell>{goodsReceipt?.vendorName || 'N/A'}</TableCell>
                <TableCell>-</TableCell>
                <TableCell>{invoice.vendorName}</TableCell>
              </TableRow>

              {/* Material / Line Items Count */}
              <TableRow>
                <TableCell>
                  <strong>Line Items</strong>
                </TableCell>
                <TableCell>
                  {purchaseOrder?.items?.length || 0} item{purchaseOrder?.items?.length !== 1 ? 's' : ''}
                </TableCell>
                <TableCell>-</TableCell>
                <TableCell>-</TableCell>
                <TableCell>
                  {invoice.lineItems?.length || 0} item{invoice.lineItems?.length !== 1 ? 's' : ''}
                </TableCell>
              </TableRow>

              {/* Total Quantity */}
              <TableRow
                sx={{
                  backgroundColor:
                    Math.abs(quantityVariance) > 2 ? oilGasTheme.colors.danger + '10' : 'transparent',
                }}
              >
                <TableCell>
                  <strong>Total Quantity</strong>
                </TableCell>
                <TableCell>
                  {totalPoQty > 0 ? totalPoQty.toLocaleString() : 'N/A'}
                </TableCell>
                <TableCell>
                  {goodsReceipt?.quantityReceived ? goodsReceipt.quantityReceived.toLocaleString() : 'N/A'}
                </TableCell>
                <TableCell>-</TableCell>
                <TableCell>
                  {totalInvoiceQty > 0 ? totalInvoiceQty.toLocaleString() : 'N/A'}
                  {Math.abs(quantityVariance) > 2 && (
                    <Typography variant="caption" sx={{ color: oilGasTheme.colors.danger, ml: 1 }}>
                      ({quantityVariance > 0 ? '+' : ''}
                      {quantityVariance.toFixed(2)}%)
                    </Typography>
                  )}
                </TableCell>
              </TableRow>

              {/* Average Unit Price (informational) */}
              <TableRow>
                <TableCell>
                  <strong>Avg Unit Price</strong>
                </TableCell>
                <TableCell>
                  {purchaseOrder?.totalAmount && totalPoQty > 0 
                    ? formatCurrency(purchaseOrder.totalAmount / totalPoQty) 
                    : 'N/A'}
                </TableCell>
                <TableCell>-</TableCell>
                <TableCell>-</TableCell>
                <TableCell>
                  {invoice.totalAmount && totalInvoiceQty > 0 
                    ? formatCurrency(invoice.totalAmount / totalInvoiceQty) 
                    : 'N/A'}
                </TableCell>
              </TableRow>

              {/* Quality Status */}
              <TableRow
                sx={{
                  backgroundColor:
                    qcRecord?.overallStatus === 'FAIL' ? oilGasTheme.colors.danger + '10' : 'transparent',
                }}
              >
                <TableCell>
                  <strong>Quality Status</strong>
                </TableCell>
                <TableCell>-</TableCell>
                <TableCell>-</TableCell>
                <TableCell>
                  {qcRecord ? (
                    <Chip
                      label={qcRecord.overallStatus}
                      size="small"
                      sx={{
                        backgroundColor:
                          qcRecord.overallStatus === 'PASS'
                            ? oilGasTheme.colors.success
                            : oilGasTheme.colors.danger,
                        color: '#fff',
                      }}
                    />
                  ) : (
                    'N/A'
                  )}
                </TableCell>
                <TableCell>-</TableCell>
              </TableRow>

              {/* Total Amount */}
              <TableRow>
                <TableCell>
                  <strong>Total Amount</strong>
                </TableCell>
                <TableCell>{purchaseOrder ? formatCurrency(purchaseOrder.totalAmount) : 'N/A'}</TableCell>
                <TableCell>-</TableCell>
                <TableCell>-</TableCell>
                <TableCell sx={{ fontWeight: 'bold' }}>{formatCurrency(invoice.totalAmount)}</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      {/* Validation Results */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Validation Results
        </Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {/* Quantity Validation */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            {Math.abs(quantityVariance) <= 2 ? (
              <CheckCircle sx={{ color: oilGasTheme.colors.success }} />
            ) : (
              <ErrorIcon sx={{ color: oilGasTheme.colors.danger }} />
            )}
            <Box>
              <Typography variant="body1">
                <strong>Quantity Match:</strong>{' '}
                {Math.abs(quantityVariance) <= 2 ? 'PASS' : 'FAIL'}
              </Typography>
              <Typography variant="caption" color="textSecondary">
                Variance: {quantityVariance.toFixed(2)}% (Tolerance: ±2%)
              </Typography>
            </Box>
          </Box>

          {/* Price/Amount Validation */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            {Math.abs(priceVariance) <= 10 ? (
              <CheckCircle sx={{ color: oilGasTheme.colors.success }} />
            ) : (
              <ErrorIcon sx={{ color: oilGasTheme.colors.danger }} />
            )}
            <Box>
              <Typography variant="body1">
                <strong>Amount Match:</strong> {Math.abs(priceVariance) <= 10 ? 'PASS' : 'FAIL'}
              </Typography>
              <Typography variant="caption" color="textSecondary">
                Variance: {priceVariance.toFixed(2)}% (Tolerance: ±10%)
              </Typography>
            </Box>
          </Box>

          {/* Quality Validation */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            {qcRecord?.overallStatus === 'PASS' ? (
              <CheckCircle sx={{ color: oilGasTheme.colors.success }} />
            ) : qcRecord?.overallStatus === 'FAIL' ? (
              <ErrorIcon sx={{ color: oilGasTheme.colors.danger }} />
            ) : (
              <Warning sx={{ color: oilGasTheme.colors.warning }} />
            )}
            <Box>
              <Typography variant="body1">
                <strong>Quality Compliance:</strong>{' '}
                {qcRecord ? qcRecord.overallStatus : 'PENDING'}
              </Typography>
              <Typography variant="caption" color="textSecondary">
                {qcRecord
                  ? `QC Record: ${qcRecord.qcNumber}`
                  : 'No quality control record found'}
              </Typography>
            </Box>
          </Box>
        </Box>
      </Paper>

      {/* Actions */}
      <Paper sx={{ p: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Actions
        </Typography>
        
        {/* 3-Way Match Requirements Alert */}
        {(!invoice.hasPoMatch || !invoice.purchaseOrderId || !invoice.goodsReceiptId) && (
          <Alert severity="error" sx={{ mb: 2 }}>
            <Typography variant="body2" fontWeight={600}>
              Payment Blocked - 3-Way Match Required
            </Typography>
            <Box component="ul" sx={{ mt: 1, mb: 0, pl: 2 }}>
              <li style={{ color: invoice.hasPoMatch && invoice.purchaseOrderId ? '#2e7d32' : '#d32f2f' }}>
                {invoice.hasPoMatch && invoice.purchaseOrderId ? '✓' : '✗'} Purchase Order matched
              </li>
              <li style={{ color: invoice.goodsReceiptId ? '#2e7d32' : '#d32f2f' }}>
                {invoice.goodsReceiptId ? '✓' : '✗'} Goods Receipt matched
              </li>
            </Box>
          </Alert>
        )}
        
        {/* Variance Warning */}
        {invoice.hasPoMatch && invoice.purchaseOrderId && invoice.goodsReceiptId && invoice.hasVarianceWarning && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            <Typography variant="body2" fontWeight={600}>
              ⚠️ Payment Eligible with Warnings
            </Typography>
            <Typography variant="body2">
              Amount variance: {invoice.variancePercentage != null ? Number(invoice.variancePercentage).toFixed(2) : '0.00'}% (exceeds 10% tolerance)
            </Typography>
            {invoice.validationFlags.length > 0 && (
              <Box sx={{ mt: 1 }}>
                <Typography variant="caption">Issues: {invoice.validationFlags.join(', ')}</Typography>
              </Box>
            )}
          </Alert>
        )}
        
        <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
          {/* Process Payment Button */}
          <Button
            variant="contained"
            startIcon={processingPayment ? <CircularProgress size={20} color="inherit" /> : <PaymentIcon />}
            onClick={handleProcessPayment}
            disabled={
              processingPayment || 
              !!paymentResult || 
              !invoice.hasPoMatch || 
              !invoice.purchaseOrderId || 
              !invoice.goodsReceiptId
            }
            sx={{
              backgroundColor: invoice.hasVarianceWarning ? '#ed6c02' : '#1A1F71', // Orange for warnings, Visa blue otherwise
              '&:hover': { 
                backgroundColor: invoice.hasVarianceWarning ? '#e65100' : '#00308F' 
              },
              '&:disabled': {
                backgroundColor: '#e0e0e0',
                color: '#9e9e9e',
              }
            }}
          >
            {processingPayment 
              ? 'Processing Payment...' 
              : paymentResult 
              ? 'Payment Processed' 
              : !invoice.hasPoMatch || !invoice.purchaseOrderId || !invoice.goodsReceiptId
              ? 'Payment Blocked'
              : invoice.hasVarianceWarning
              ? 'Process Payment (Warnings)'
              : 'Process Payment'}
          </Button>

          {/* Only show Download ISO20022 button if payment method is ISO20022 and file exists */}
          {paymentResult?.payment_method === 'iso20022' && (paymentResult?.file_key || invoice.iso20022FileKey) && (
            <Button
              variant="contained"
              onClick={async () => {
                if (!invoice.iso20022FileKey) {
                  alert('No ISO20022 payment file available for this invoice');
                  return;
                }
                try {
                  const response = await fetch(`${API_BASE_URL}/api/invoices/${invoice.id}/download-iso20022`);
                  if (!response.ok) throw new Error('Failed to get download URL');
                  const data = await response.json();
                  window.open(data.downloadUrl, '_blank');
                } catch (err) {
                  console.error('Download error:', err);
                  alert('Failed to download ISO20022 file');
                }
              }}
              sx={{
                backgroundColor: oilGasTheme.colors.success,
                '&:hover': { backgroundColor: '#1e7e34' },
              }}
            >
              Download ISO20022 Payment File
            </Button>
          )}
          <Button
            variant="outlined"
            sx={{
              color: oilGasTheme.colors.danger,
              borderColor: oilGasTheme.colors.danger,
            }}
          >
            Reject Invoice
          </Button>
          <Button variant="outlined">Request Correction</Button>
        </Box>

        {/* Payment Error */}
        {paymentError && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {paymentError}
          </Alert>
        )}

        {/* Payment Result with Agent Reasoning */}
        {paymentResult && (
          <Box sx={{ mt: 2 }}>
            {paymentResult.agent_reasoning && (
              <Alert severity="info" sx={{ mb: 2 }}>
                <Typography variant="body2" fontWeight={600}>
                  Payment Agent Decision:
                </Typography>
                <Typography variant="body2">
                  {paymentResult.agent_reasoning}
                </Typography>
              </Alert>
            )}
            
            {paymentResult.payment_method && (
              <Box sx={{ mb: 2 }}>
                <PaymentStatusBadge 
                  status={paymentResult.status || 'card_issued'} 
                  paymentMethod={paymentResult.payment_method}
                />
              </Box>
            )}
          </Box>
        )}

        {invoice.iso20022FileKey && (
          <Alert severity="info" sx={{ mt: 2 }}>
            ISO20022 payment file is ready for download
          </Alert>
        )}
      </Paper>

      {/* Virtual Card Display */}
      {paymentResult?.cardDetails && (
        <VirtualCardDisplay
          cardNumber={paymentResult.cardDetails.cardNumber}
          cvv={paymentResult.cardDetails.cvv}
          expiry={paymentResult.cardDetails.expiry}
          maskedCardNumber={paymentResult.cardDetails.maskedCardNumber}
          amount={paymentResult.cardDetails.amount || invoice.totalAmount}
          currency={paymentResult.cardDetails.currency || 'USD'}
          paymentId={paymentResult.cardDetails.paymentId || paymentResult.payment_id}
          trackingNumber={paymentResult.cardDetails.trackingNumber || paymentResult.tracking_number}
          paymentMethod={paymentResult.payment_method}
        />
      )}
    </Box>
  );
};
