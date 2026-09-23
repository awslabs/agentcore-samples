import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  Paper,
  Alert,
  List,
  ListItem,
  ListItemText,
  IconButton,
  Divider,
  Autocomplete,
  TextField,
  CircularProgress,
} from '@mui/material';
import { CloudUpload, Delete, Description } from '@mui/icons-material';
import { Button } from '../common/Button';
import * as apiService from '../../services/api.service';
import type { PurchaseOrder } from '../../types/data.types';

interface UploadedFile {
  file: File;
  preview?: string;
}

export const UploadReceipt: React.FC = () => {
  const navigate = useNavigate();
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [purchaseOrders, setPurchaseOrders] = useState<PurchaseOrder[]>([]);
  const [selectedPO, setSelectedPO] = useState<PurchaseOrder | null>(null);
  const [loadingPOs, setLoadingPOs] = useState(true);
  const [quantityReceived, setQuantityReceived] = useState('');
  const [deliveryDate, setDeliveryDate] = useState(new Date().toISOString().split('T')[0]);
  const [deliveryReference, setDeliveryReference] = useState('');
  const [conditionNotes, setConditionNotes] = useState('');

  useEffect(() => {
    loadPurchaseOrders();
  }, []);

  const loadPurchaseOrders = async () => {
    try {
      setLoadingPOs(true);
      const pos = await apiService.getPurchaseOrdersEnhanced({ status: ['active'] });
      setPurchaseOrders(pos);
    } catch (err) {
      setError('Failed to load purchase orders');
    } finally {
      setLoadingPOs(false);
    }
  };

  const acceptedFileTypes = ['application/pdf', 'image/png', 'image/jpeg', 'image/jpg'];
  const maxFileSize = 10 * 1024 * 1024; // 10MB

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files) return;

    setError('');
    const newFiles: UploadedFile[] = [];

    Array.from(files).forEach((file) => {
      // Validate file type
      if (!acceptedFileTypes.includes(file.type)) {
        setError(`Invalid file type: ${file.name}. Only PDF, PNG, and JPEG files are allowed.`);
        return;
      }

      // Validate file size
      if (file.size > maxFileSize) {
        setError(`File too large: ${file.name}. Maximum size is 10MB.`);
        return;
      }

      // Create preview for images
      let preview: string | undefined;
      if (file.type.startsWith('image/')) {
        preview = URL.createObjectURL(file);
      }

      newFiles.push({ file, preview });
    });

    setUploadedFiles([...uploadedFiles, ...newFiles]);
  };

  const handleRemoveFile = (index: number) => {
    const newFiles = [...uploadedFiles];
    // Revoke object URL to prevent memory leaks
    if (newFiles[index].preview) {
      URL.revokeObjectURL(newFiles[index].preview!);
    }
    newFiles.splice(index, 1);
    setUploadedFiles(newFiles);
  };

  const handleSubmit = async () => {
    if (!selectedPO) {
      setError('Please select a purchase order');
      return;
    }

    if (uploadedFiles.length === 0) {
      setError('Please upload at least one receipt document');
      return;
    }

    if (!quantityReceived || parseFloat(quantityReceived) <= 0) {
      setError('Please enter a valid quantity received');
      return;
    }

    if (!deliveryReference.trim()) {
      setError('Please enter a delivery reference');
      return;
    }

    try {
      // Upload the first file (in a real app, you might handle multiple files differently)
      await apiService.uploadReceiptForPO(selectedPO.id, uploadedFiles[0].file, {
        quantityReceived: parseFloat(quantityReceived),
        deliveryDate,
        deliveryReference,
        conditionNotes: conditionNotes || undefined,
      });

      setSuccess(true);
      setTimeout(() => {
        navigate('/delivery/purchase-orders');
      }, 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload receipt');
    }
  };

  const handleCancel = () => {
    // Clean up object URLs
    uploadedFiles.forEach((file) => {
      if (file.preview) {
        URL.revokeObjectURL(file.preview);
      }
    });
    navigate('/delivery');
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
  };

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Upload Receipt Documents
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Upload delivery receipt documents (PDF, PNG, JPEG) - Maximum 10MB per file
      </Typography>

      {success && (
        <Alert severity="success" sx={{ mb: 3 }}>
          Receipt documents uploaded successfully! Redirecting...
        </Alert>
      )}

      {error && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError('')}>
          {error}
        </Alert>
      )}

      {/* PO Selection */}
      <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" gutterBottom>
          1. Select Purchase Order
        </Typography>
        <Autocomplete
          options={purchaseOrders}
          getOptionLabel={(option) => `${option.poNumber} - ${option.vendor.name}`}
          value={selectedPO}
          onChange={(_, newValue) => setSelectedPO(newValue)}
          loading={loadingPOs}
          renderInput={(params) => (
            <TextField
              {...params}
              label="Purchase Order"
              placeholder="Search by PO number or vendor"
              InputProps={{
                ...params.InputProps,
                endAdornment: (
                  <>
                    {loadingPOs ? <CircularProgress color="inherit" size={20} /> : null}
                    {params.InputProps.endAdornment}
                  </>
                ),
              }}
            />
          )}
          renderOption={(props, option) => (
            <li {...props}>
              <Box>
                <Typography variant="body1">{option.poNumber}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {option.vendor.name} • Ordered: {option.orderedQuantity} • Received: {option.receivedQuantity}
                </Typography>
              </Box>
            </li>
          )}
        />

        {selectedPO && (
          <Box sx={{ mt: 2, p: 2, bgcolor: 'grey.50', borderRadius: 1 }}>
            <Typography variant="subtitle2" gutterBottom>
              Selected PO Details:
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Vendor: {selectedPO.vendor.name}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Ordered Quantity: {selectedPO.orderedQuantity}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Already Received: {selectedPO.receivedQuantity}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Remaining: {selectedPO.orderedQuantity - selectedPO.receivedQuantity}
            </Typography>
          </Box>
        )}
      </Paper>

      {/* Receipt Information */}
      {selectedPO && (
        <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
          <Typography variant="h6" gutterBottom>
            2. Enter Receipt Information
          </Typography>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <TextField
              label="Quantity Received"
              type="number"
              value={quantityReceived}
              onChange={(e) => setQuantityReceived(e.target.value)}
              inputProps={{ min: 0, step: 1 }}
              required
              fullWidth
            />
            <TextField
              label="Delivery Date"
              type="date"
              value={deliveryDate}
              onChange={(e) => setDeliveryDate(e.target.value)}
              InputLabelProps={{ shrink: true }}
              required
              fullWidth
            />
            <TextField
              label="Delivery Reference"
              value={deliveryReference}
              onChange={(e) => setDeliveryReference(e.target.value)}
              placeholder="e.g., Tracking number, delivery note number"
              required
              fullWidth
            />
            <TextField
              label="Condition Notes (Optional)"
              value={conditionNotes}
              onChange={(e) => setConditionNotes(e.target.value)}
              multiline
              rows={3}
              placeholder="Any notes about the condition of received items"
              fullWidth
            />
          </Box>
        </Paper>
      )}

      {/* File Upload */}
      {selectedPO && (
        <Paper elevation={2} sx={{ p: 4, mb: 3 }}>
          <Typography variant="h6" gutterBottom>
            3. Upload Receipt Document
          </Typography>
        <Box
          sx={{
            border: '2px dashed',
            borderColor: 'primary.main',
            borderRadius: 2,
            p: 4,
            textAlign: 'center',
            backgroundColor: (theme) => theme.palette.grey[50],
            cursor: 'pointer',
            transition: 'all 0.3s',
            '&:hover': {
              backgroundColor: (theme) => theme.palette.grey[100],
            },
          }}
          onClick={() => document.getElementById('file-input')?.click()}
        >
          <CloudUpload sx={{ fontSize: 60, color: 'primary.main', mb: 2 }} />
          <Typography variant="h6" gutterBottom>
            Click to upload or drag and drop
          </Typography>
          <Typography variant="body2" color="text.secondary">
            PDF, PNG, JPEG (max 10MB per file)
          </Typography>
          <input
            id="file-input"
            type="file"
            multiple
            accept=".pdf,.png,.jpg,.jpeg"
            onChange={handleFileSelect}
            style={{ display: 'none' }}
          />
          </Box>

          {uploadedFiles.length > 0 && (
          <>
            <Divider sx={{ my: 3 }} />
            <Typography variant="h6" gutterBottom>
              Uploaded Files ({uploadedFiles.length})
            </Typography>
            <List>
              {uploadedFiles.map((uploadedFile, index) => (
                <ListItem
                  key={index}
                  secondaryAction={
                    <IconButton
                      edge="end"
                      aria-label="delete"
                      onClick={() => handleRemoveFile(index)}
                    >
                      <Delete />
                    </IconButton>
                  }
                >
                  <Description sx={{ mr: 2, color: 'primary.main' }} />
                  <ListItemText
                    primary={uploadedFile.file.name}
                    secondary={`${formatFileSize(uploadedFile.file.size)} - ${uploadedFile.file.type}`}
                  />
                </ListItem>
              ))}
              </List>
            </>
          )}
        </Paper>
      )}

      <Box sx={{ display: 'flex', gap: 2, justifyContent: 'flex-end' }}>
        <Button variant="secondary" onClick={handleCancel} disabled={success}>
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={handleSubmit}
          disabled={uploadedFiles.length === 0 || success}
        >
          {success ? 'Uploading...' : 'Submit'}
        </Button>
      </Box>
    </Box>
  );
};
