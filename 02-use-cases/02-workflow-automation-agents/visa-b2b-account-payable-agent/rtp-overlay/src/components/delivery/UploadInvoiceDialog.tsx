import React, { useState, useCallback } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Typography,
  LinearProgress,
  Alert,
  Paper,
  IconButton,
  TextField,
  Grid,
} from '@mui/material';
import {
  CloudUpload as CloudUploadIcon,
  Close as CloseIcon,
  Description as DescriptionIcon,
} from '@mui/icons-material';
import { useDropzone } from 'react-dropzone';
import type { PurchaseOrder } from '../../types/data.types';

interface UploadInvoiceDialogProps {
  open: boolean;
  purchaseOrder: PurchaseOrder | null;
  onClose: () => void;
  onUploadSuccess: (invoiceId: string) => void;
  onUpload: (
    poId: string,
    file: File,
    metadata: {
      invoiceNumber: string;
      invoiceAmount: number;
      invoiceDate: string;
    }
  ) => Promise<{ message: string; invoiceId: string }>;
}

export const UploadInvoiceDialog: React.FC<UploadInvoiceDialogProps> = ({
  open,
  purchaseOrder,
  onClose,
  onUploadSuccess,
  onUpload,
}) => {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  
  // Form fields
  const [invoiceNumber, setInvoiceNumber] = useState('');
  const [invoiceAmount, setInvoiceAmount] = useState('');
  const [invoiceDate, setInvoiceDate] = useState(
    new Date().toISOString().split('T')[0]
  );
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});

  const onDrop = useCallback((acceptedFiles: File[]) => {
    const file = acceptedFiles[0];
    if (file) {
      // Validate file type
      const allowedTypes = ['application/pdf', 'image/png', 'image/jpeg'];
      if (!allowedTypes.includes(file.type)) {
        setError('Please select a PDF, PNG, or JPG file.');
        return;
      }

      // Validate file size (10MB limit)
      const maxSize = 10 * 1024 * 1024; // 10MB
      if (file.size > maxSize) {
        setError('File size must be less than 10MB.');
        return;
      }

      setSelectedFile(file);
      setError(null);
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'image/png': ['.png'],
      'image/jpeg': ['.jpg', '.jpeg'],
    },
    multiple: false,
  });

  const validateForm = (): boolean => {
    const errors: Record<string, string> = {};

    if (!invoiceNumber.trim()) {
      errors.invoiceNumber = 'Invoice number is required';
    }

    if (!invoiceAmount || parseFloat(invoiceAmount) <= 0) {
      errors.invoiceAmount = 'Valid invoice amount is required';
    }

    if (!invoiceDate) {
      errors.invoiceDate = 'Invoice date is required';
    }

    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const calculateVariance = (): number | null => {
    if (!purchaseOrder || !invoiceAmount) return null;
    const amount = parseFloat(invoiceAmount);
    return Math.abs((amount - purchaseOrder.totalAmount) / purchaseOrder.totalAmount) * 100;
  };

  const variance = calculateVariance();
  const hasHighVariance = variance !== null && variance > 10;

  const handleUpload = async () => {
    if (!selectedFile || !purchaseOrder) return;

    if (!validateForm()) {
      return;
    }

    setUploading(true);
    setUploadProgress(0);
    setError(null);

    try {
      // Simulate upload progress
      const progressInterval = setInterval(() => {
        setUploadProgress((prev) => {
          if (prev >= 90) {
            clearInterval(progressInterval);
            return 90;
          }
          return prev + 10;
        });
      }, 200);

      const response = await onUpload(purchaseOrder.id, selectedFile, {
        invoiceNumber,
        invoiceAmount: parseFloat(invoiceAmount),
        invoiceDate,
      });

      clearInterval(progressInterval);
      setUploadProgress(100);

      setTimeout(() => {
        onUploadSuccess(response.invoiceId);
        handleClose();
      }, 500);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Upload failed';
      setError(message);
    } finally {
      setUploading(false);
    }
  };

  const handleClose = () => {
    if (!uploading) {
      setSelectedFile(null);
      setError(null);
      setUploadProgress(0);
      setInvoiceNumber('');
      setInvoiceAmount('');
      setInvoiceDate(new Date().toISOString().split('T')[0]);
      setFormErrors({});
      onClose();
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <Box display="flex" alignItems="center" justifyContent="space-between">
          <Typography variant="h6">Upload Invoice</Typography>
          <IconButton onClick={handleClose} disabled={uploading}>
            <CloseIcon />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent>
        {purchaseOrder && (
          <Box sx={{ mb: 3, p: 2, bgcolor: 'grey.50', borderRadius: 1 }}>
            <Typography variant="subtitle2" gutterBottom>
              Purchase Order: {purchaseOrder.poNumber}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Vendor: {purchaseOrder.vendor.name}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              PO Amount: {purchaseOrder.currency} {purchaseOrder.totalAmount.toLocaleString()}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Remaining: {purchaseOrder.currency} {purchaseOrder.remainingAmount.toLocaleString()}
            </Typography>
          </Box>
        )}

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {hasHighVariance && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            Warning: Invoice amount differs from PO by {variance?.toFixed(1)}%. This may require approval.
          </Alert>
        )}

        {/* Invoice Metadata Form */}
        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle1" gutterBottom fontWeight={600}>
            Invoice Information
          </Typography>
          <Grid container spacing={2}>
            <Grid size={{ xs: 12, sm: 6 }}>
              <TextField
                fullWidth
                label="Invoice Number"
                value={invoiceNumber}
                onChange={(e) => setInvoiceNumber(e.target.value)}
                error={!!formErrors.invoiceNumber}
                helperText={formErrors.invoiceNumber}
                placeholder="e.g., INV-2024-001"
                required
              />
            </Grid>
            <Grid size={{ xs: 12, sm: 6 }}>
              <TextField
                fullWidth
                label="Invoice Date"
                type="date"
                value={invoiceDate}
                onChange={(e) => setInvoiceDate(e.target.value)}
                error={!!formErrors.invoiceDate}
                helperText={formErrors.invoiceDate}
                InputLabelProps={{ shrink: true }}
                required
              />
            </Grid>
            <Grid size={{ xs: 12 }}>
              <TextField
                fullWidth
                label="Invoice Amount"
                type="number"
                value={invoiceAmount}
                onChange={(e) => setInvoiceAmount(e.target.value)}
                error={!!formErrors.invoiceAmount}
                helperText={formErrors.invoiceAmount || (variance !== null ? `Variance: ${variance.toFixed(1)}%` : '')}
                inputProps={{ min: 0, step: 0.01 }}
                required
              />
            </Grid>
          </Grid>
        </Box>

        {/* File Upload Section */}
        <Box>
          <Typography variant="subtitle1" gutterBottom fontWeight={600}>
            Invoice Document
          </Typography>
          {!selectedFile ? (
            <Paper
              {...getRootProps()}
              sx={{
                border: '2px dashed',
                borderColor: isDragActive ? 'primary.main' : 'grey.300',
                borderRadius: 2,
                p: 4,
                textAlign: 'center',
                cursor: 'pointer',
                bgcolor: isDragActive ? 'action.hover' : 'background.paper',
                '&:hover': {
                  bgcolor: 'action.hover',
                  borderColor: 'primary.main',
                },
              }}
            >
              <input {...getInputProps()} />
              <CloudUploadIcon sx={{ fontSize: 48, color: 'text.secondary', mb: 2 }} />
              <Typography variant="h6" gutterBottom>
                {isDragActive ? 'Drop the file here' : 'Drag & drop invoice file here'}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                or click to select a file
              </Typography>
              <Typography variant="caption" display="block" sx={{ mt: 1 }}>
                Supported formats: PDF, PNG, JPG (max 10MB)
              </Typography>
            </Paper>
          ) : (
            <Paper sx={{ p: 2, bgcolor: 'grey.50' }}>
              <Box display="flex" alignItems="center" gap={2}>
                <DescriptionIcon color="primary" />
                <Box flex={1}>
                  <Typography variant="subtitle2">{selectedFile.name}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {formatFileSize(selectedFile.size)}
                  </Typography>
                </Box>
                {!uploading && (
                  <Button
                    size="small"
                    onClick={() => setSelectedFile(null)}
                    color="error"
                  >
                    Remove
                  </Button>
                )}
              </Box>

              {uploading && (
                <Box sx={{ mt: 2 }}>
                  <Box display="flex" alignItems="center" justifyContent="space-between" mb={1}>
                    <Typography variant="body2">Uploading...</Typography>
                    <Typography variant="body2">{Math.round(uploadProgress)}%</Typography>
                  </Box>
                  <LinearProgress variant="determinate" value={uploadProgress} />
                </Box>
              )}
            </Paper>
          )}
        </Box>
      </DialogContent>

      <DialogActions>
        <Button onClick={handleClose} disabled={uploading}>
          Cancel
        </Button>
        <Button
          onClick={handleUpload}
          variant="contained"
          disabled={!selectedFile || uploading}
          startIcon={<CloudUploadIcon />}
        >
          {uploading ? 'Uploading...' : 'Upload Invoice'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
