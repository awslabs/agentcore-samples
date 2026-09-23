import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Box,
  Typography,
  TextField,
  Paper,
  Alert,
  Divider,
  Grid,
  ToggleButtonGroup,
  ToggleButton,
} from '@mui/material';
import { QrCodeScanner, Edit } from '@mui/icons-material';
import { Button } from '../common/Button';
import { LoadingSpinner } from '../common/LoadingSpinner';
import { useAuth } from '../../contexts/AuthContext';
import { getPurchaseOrderById, createGoodsReceipt } from '../../services/api.service';
import type { PurchaseOrder, GoodsReceiptData } from '../../types/data.types';

export const GoodsReceiptCapture: React.FC = () => {
  const { orderId } = useParams<{ orderId: string }>();
  const navigate = useNavigate();
  const { user } = useAuth();

  const [order, setOrder] = useState<PurchaseOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState('');
  const [captureMethod, setCaptureMethod] = useState<'manual' | 'barcode'>('manual');

  const [formData, setFormData] = useState({
    deliveryDate: new Date().toISOString().split('T')[0],
    quantityReceived: '',
    conditionNotes: '',
    deliveryReference: '',
  });

  const [formErrors, setFormErrors] = useState({
    deliveryDate: '',
    quantityReceived: '',
    deliveryReference: '',
  });

  useEffect(() => {
    loadOrder();
  }, [orderId]);

  const loadOrder = async () => {
    if (!orderId) return;

    try {
      setLoading(true);
      const data = await getPurchaseOrderById(orderId);
      if (data) {
        setOrder(data);
        // Pre-fill quantity with PO quantity
        setFormData((prev) => ({
          ...prev,
          quantityReceived: data.items[0]?.quantity.toString() || '',
        }));
      } else {
        setError('Purchase order not found');
      }
    } catch (err) {
      setError('Failed to load purchase order');
    } finally {
      setLoading(false);
    }
  };

  const validateForm = (): boolean => {
    const errors = {
      deliveryDate: '',
      quantityReceived: '',
      deliveryReference: '',
    };

    if (!formData.deliveryDate) {
      errors.deliveryDate = 'Delivery date is required';
    }

    if (!formData.quantityReceived) {
      errors.quantityReceived = 'Quantity received is required';
    } else if (isNaN(Number(formData.quantityReceived)) || Number(formData.quantityReceived) <= 0) {
      errors.quantityReceived = 'Quantity must be a positive number';
    }

    if (!formData.deliveryReference) {
      errors.deliveryReference = 'Delivery reference is required';
    }

    setFormErrors(errors);
    return !Object.values(errors).some((error) => error !== '');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!validateForm() || !order || !user) return;

    try {
      setSubmitting(true);
      setError('');

      const receiptData: GoodsReceiptData = {
        deliveryDate: new Date(formData.deliveryDate),
        quantityReceived: Number(formData.quantityReceived),
        conditionNotes: formData.conditionNotes,
        deliveryReference: formData.deliveryReference,
      };

      await createGoodsReceipt(order.id, receiptData, user.name);
      setSuccess(true);

      // Redirect after 2 seconds
      setTimeout(() => {
        navigate('/delivery/purchase-orders');
      }, 2000);
    } catch (err) {
      setError('Failed to create goods receipt. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = () => {
    navigate('/delivery/purchase-orders');
  };

  if (loading) {
    return <LoadingSpinner message="Loading purchase order..." />;
  }

  if (!order) {
    return (
      <Box>
        <Alert severity="error">Purchase order not found</Alert>
        <Button variant="secondary" onClick={handleCancel} sx={{ mt: 2 }}>
          Back to List
        </Button>
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Capture Goods Receipt
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Record delivery details for purchase order {order.poNumber}
      </Typography>

      {success && (
        <Alert severity="success" sx={{ mb: 3 }}>
          Goods receipt created successfully! Redirecting...
        </Alert>
      )}

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" gutterBottom>
          Purchase Order Details
        </Typography>
        <Grid container spacing={2}>
          <Grid size={{ xs: 12, sm: 6 }}>
            <Typography variant="body2" color="text.secondary">
              PO Number
            </Typography>
            <Typography variant="body1">{order.poNumber}</Typography>
          </Grid>
          <Grid size={{ xs: 12, sm: 6 }}>
            <Typography variant="body2" color="text.secondary">
              Vendor
            </Typography>
            <Typography variant="body1">{order.vendor.name}</Typography>
          </Grid>
          <Grid size={{ xs: 12, sm: 6 }}>
            <Typography variant="body2" color="text.secondary">
              Source Location
            </Typography>
            <Typography variant="body1">{order.sourceLocation}</Typography>
          </Grid>
          <Grid size={{ xs: 12, sm: 6 }}>
            <Typography variant="body2" color="text.secondary">
              Expected Quantity
            </Typography>
            <Typography variant="body1">{order.items[0]?.quantity || 'N/A'}</Typography>
          </Grid>
        </Grid>
      </Paper>

      <Paper elevation={2} sx={{ p: 3 }}>
        <Box sx={{ mb: 3 }}>
          <Typography variant="h6" gutterBottom>
            Capture Method
          </Typography>
          <ToggleButtonGroup
            value={captureMethod}
            exclusive
            onChange={(_e, value) => value && setCaptureMethod(value)}
            aria-label="capture method"
          >
            <ToggleButton value="manual" aria-label="manual entry">
              <Edit sx={{ mr: 1 }} />
              Manual Entry
            </ToggleButton>
            <ToggleButton value="barcode" aria-label="barcode scan" disabled>
              <QrCodeScanner sx={{ mr: 1 }} />
              Barcode Scan (Coming Soon)
            </ToggleButton>
          </ToggleButtonGroup>
        </Box>

        <Divider sx={{ my: 3 }} />

        <form onSubmit={handleSubmit}>
          <Grid container spacing={3}>
            <Grid size={{ xs: 12, sm: 6 }}>
              <TextField
                fullWidth
                label="Delivery Date"
                type="date"
                value={formData.deliveryDate}
                onChange={(e) => setFormData({ ...formData, deliveryDate: e.target.value })}
                error={!!formErrors.deliveryDate}
                helperText={formErrors.deliveryDate}
                required
                InputLabelProps={{ shrink: true }}
              />
            </Grid>

            <Grid size={{ xs: 12, sm: 6 }}>
              <TextField
                fullWidth
                label="Quantity Received"
                type="number"
                value={formData.quantityReceived}
                onChange={(e) => setFormData({ ...formData, quantityReceived: e.target.value })}
                error={!!formErrors.quantityReceived}
                helperText={formErrors.quantityReceived}
                required
              />
            </Grid>

            <Grid size={{ xs: 12 }}>
              <TextField
                fullWidth
                label="Delivery Reference"
                value={formData.deliveryReference}
                onChange={(e) => setFormData({ ...formData, deliveryReference: e.target.value })}
                error={!!formErrors.deliveryReference}
                helperText={formErrors.deliveryReference}
                required
                placeholder="e.g., BOL-12345, Tracking-ABC123"
              />
            </Grid>

            <Grid size={{ xs: 12 }}>
              <TextField
                fullWidth
                label="Condition Notes"
                multiline
                rows={4}
                value={formData.conditionNotes}
                onChange={(e) => setFormData({ ...formData, conditionNotes: e.target.value })}
                placeholder="Enter any notes about the delivery condition, packaging, etc."
              />
            </Grid>
          </Grid>

          <Box sx={{ mt: 3, display: 'flex', gap: 2, justifyContent: 'flex-end' }}>
            <Button variant="secondary" onClick={handleCancel} disabled={submitting}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={submitting}>
              {submitting ? 'Submitting...' : 'Submit Receipt'}
            </Button>
          </Box>
        </form>
      </Paper>
    </Box>
  );
};
