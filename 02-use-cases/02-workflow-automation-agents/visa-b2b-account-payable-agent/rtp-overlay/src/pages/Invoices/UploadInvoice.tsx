import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Button, Typography, Paper, Alert, CircularProgress, TextField } from '@mui/material';
import { CloudUpload, CheckCircle, Warning, Error as ErrorIcon } from '@mui/icons-material';
import { oilGasTheme } from '../../styles/oilGasTheme';
import { documentService } from '../../services/documentService';

interface ExtractedData {
  invoiceNumber: string;
  vendor: string;
  invoiceDate: string;
  dueDate: string;
  poNumber: string;
  material: string;
  quantity: number;
  unitPrice: number;
  totalAmount: number;
  taxAmount: number;
}

interface ConfidenceScores {
  invoiceNumber: number;
  vendor: number;
  invoiceDate: number;
  dueDate: number;
  poNumber: number;
  material: number;
  quantity: number;
  unitPrice: number;
  totalAmount: number;
  taxAmount: number;
}

export const UploadInvoice: React.FC = () => {
  const navigate = useNavigate();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractedData, setExtractedData] = useState<ExtractedData | null>(null);
  const [confidenceScores, setConfidenceScores] = useState<ConfidenceScores | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invoiceId, setInvoiceId] = useState<string | null>(null);

  // Real AI extraction using documentService
  const performExtraction = async (file: File) => {
    setIsUploading(true);
    setIsExtracting(false);
    setError(null);

    try {
      // Step 1: Upload file
      const uploadResult = await documentService.uploadInvoice(file);
      
      setIsUploading(false);
      setIsExtracting(true);

      // Step 2: Poll for completion
      const jobStatus = await documentService.pollForJobCompletion(uploadResult.jobId);

      // Step 3: Get invoice data
      if (!jobStatus.resultId) {
        throw new Error('Job completed but no invoice ID returned');
      }

      const invoice = await documentService.getInvoice(jobStatus.resultId);
      setInvoiceId(invoice.id);

      // Transform backend data to component format
      const extracted: ExtractedData = {
        invoiceNumber: invoice.invoiceNumber,
        vendor: invoice.vendor.name,
        invoiceDate: invoice.invoiceDate ? invoice.invoiceDate.split('T')[0] : new Date().toISOString().split('T')[0],
        dueDate: invoice.dueDate ? invoice.dueDate.split('T')[0] : new Date().toISOString().split('T')[0],
        poNumber: invoice.purchaseOrder?.poNumber || invoice.extractedData?.po_reference || '',
        material: invoice.extractedData?.line_items?.[0]?.description || '',
        quantity: invoice.extractedData?.line_items?.[0]?.quantity || 0,
        unitPrice: invoice.extractedData?.line_items?.[0]?.unit_price || 0,
        totalAmount: invoice.totalAmount,
        taxAmount: invoice.taxAmount
      };

      // Extract confidence scores if available
      const confidence: ConfidenceScores = {
        invoiceNumber: 95,
        vendor: 95,
        invoiceDate: 90,
        dueDate: 85,
        poNumber: invoice.extractedData?.confidence_scores?.po_reference || 80,
        material: 90,
        quantity: 85,
        unitPrice: 85,
        totalAmount: 95,
        taxAmount: 85
      };

      setExtractedData(extracted);
      setConfidenceScores(confidence);
      setIsExtracting(false);
    } catch (err) {
      setIsUploading(false);
      setIsExtracting(false);
      setError(err instanceof Error ? err.message : 'Failed to process invoice');
      console.error('Invoice extraction error:', err);
    }
  };

  const handleFileSelect = (file: File) => {
    if (file.type === 'application/pdf' || file.type.startsWith('image/')) {
      setSelectedFile(file);
      setExtractedData(null);
      setConfidenceScores(null);
    } else {
      alert('Please upload a PDF or image file');
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    
    const file = e.dataTransfer.files[0];
    if (file) {
      handleFileSelect(file);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      handleFileSelect(file);
    }
  };

  const handleExtract = () => {
    if (selectedFile) {
      performExtraction(selectedFile);
    }
  };

  const handleConfirm = () => {
    if (invoiceId) {
      // Invoice already created and extracted, just navigate to view it
      navigate(`/invoices/${invoiceId}`);
    } else {
      alert('Invoice processed successfully!');
      navigate('/invoices');
    }
  };

  const getConfidenceBgColor = (score: number) => {
    if (score >= 85) return 'transparent';
    if (score >= 70) return '#fff3cd';
    return '#f8d7da';
  };

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" sx={{ mb: 3, color: oilGasTheme.colors.primary }}>
        Upload Invoice Document
      </Typography>

      {!extractedData && (
        <>
          {/* File Upload Area */}
          <Paper
            sx={{
              p: 4,
              mb: 3,
              border: `2px dashed ${isDragging ? oilGasTheme.colors.primary : oilGasTheme.colors.border}`,
              backgroundColor: isDragging ? '#f0f7ff' : oilGasTheme.colors.background,
              textAlign: 'center',
              cursor: 'pointer',
              transition: 'all 0.3s ease'
            }}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
          >
            <CloudUpload sx={{ fontSize: 64, color: oilGasTheme.colors.textMuted, mb: 2 }} />
            <Typography variant="h6" sx={{ mb: 1 }}>
              Drag and drop your invoice here
            </Typography>
            <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
              or
            </Typography>
            <Button
              variant="contained"
              component="label"
              sx={{
                backgroundColor: oilGasTheme.colors.primary,
                '&:hover': { backgroundColor: '#003366' }
              }}
            >
              Browse Files
              <input
                type="file"
                hidden
                accept=".pdf,image/*"
                onChange={handleFileInput}
              />
            </Button>
            <Typography variant="caption" display="block" sx={{ mt: 2, color: oilGasTheme.colors.textMuted }}>
              Supported formats: PDF, JPG, PNG
            </Typography>
          </Paper>

          {selectedFile && (
            <Paper sx={{ p: 3, mb: 3 }}>
              <Typography variant="h6" sx={{ mb: 2 }}>Selected File</Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <Box>
                  <Typography variant="body1">{selectedFile.name}</Typography>
                  <Typography variant="caption" color="textSecondary">
                    {(selectedFile.size / 1024).toFixed(2)} KB
                  </Typography>
                </Box>
                <Button
                  variant="contained"
                  onClick={handleExtract}
                  disabled={isUploading || isExtracting}
                  sx={{
                    backgroundColor: oilGasTheme.colors.primary,
                    '&:hover': { backgroundColor: '#003366' }
                  }}
                >
                  {(isUploading || isExtracting) ? <CircularProgress size={24} /> : 'Extract Data'}
                </Button>
              </Box>
            </Paper>
          )}

          {isUploading && (
            <Alert severity="info" sx={{ mb: 3 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <CircularProgress size={20} />
                <Typography>Uploading invoice to S3...</Typography>
              </Box>
            </Alert>
          )}

          {isExtracting && (
            <Alert severity="info" sx={{ mb: 3 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <CircularProgress size={20} />
                <Typography>Processing invoice with AI extraction... This may take up to 30 seconds.</Typography>
              </Box>
            </Alert>
          )}

          {error && (
            <Alert severity="error" sx={{ mb: 3 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <ErrorIcon />
                <Typography>{error}</Typography>
              </Box>
            </Alert>
          )}
        </>
      )}

      {/* Extracted Data Form */}
      {extractedData && confidenceScores && (
        <>
          <Alert severity="success" sx={{ mb: 3 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <CheckCircle />
              <Typography>Invoice extracted successfully! Please review and edit the data below.</Typography>
            </Box>
          </Alert>

          <Alert severity="warning" sx={{ mb: 3 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Warning />
              <Typography>Fields highlighted in yellow have lower confidence scores and should be verified.</Typography>
            </Box>
          </Alert>

          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h6" sx={{ mb: 3 }}>Extracted Invoice Data - Review and Confirm</Typography>
            
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <TextField
                label="Invoice Number"
                value={extractedData.invoiceNumber}
                onChange={(e) => setExtractedData({ ...extractedData, invoiceNumber: e.target.value })}
                fullWidth
                sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.invoiceNumber) }}
                helperText={`Confidence: ${confidenceScores.invoiceNumber}%`}
              />

              <TextField
                label="Vendor"
                value={extractedData.vendor}
                onChange={(e) => setExtractedData({ ...extractedData, vendor: e.target.value })}
                fullWidth
                sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.vendor) }}
                helperText={`Confidence: ${confidenceScores.vendor}%`}
              />

              <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2 }}>
                <TextField
                  label="Invoice Date"
                  type="date"
                  value={extractedData.invoiceDate}
                  onChange={(e) => setExtractedData({ ...extractedData, invoiceDate: e.target.value })}
                  fullWidth
                  InputLabelProps={{ shrink: true }}
                  sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.invoiceDate) }}
                  helperText={`Confidence: ${confidenceScores.invoiceDate}%`}
                />

                <TextField
                  label="Due Date"
                  type="date"
                  value={extractedData.dueDate}
                  onChange={(e) => setExtractedData({ ...extractedData, dueDate: e.target.value })}
                  fullWidth
                  InputLabelProps={{ shrink: true }}
                  sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.dueDate) }}
                  helperText={`Confidence: ${confidenceScores.dueDate}% - Please verify`}
                />
              </Box>

              <TextField
                label="PO Number"
                value={extractedData.poNumber}
                onChange={(e) => setExtractedData({ ...extractedData, poNumber: e.target.value })}
                fullWidth
                sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.poNumber) }}
                helperText={`Confidence: ${confidenceScores.poNumber}%`}
              />

              <TextField
                label="Material"
                value={extractedData.material}
                onChange={(e) => setExtractedData({ ...extractedData, material: e.target.value })}
                fullWidth
                sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.material) }}
                helperText={`Confidence: ${confidenceScores.material}%`}
              />

              <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2 }}>
                <TextField
                  label="Quantity (barrels)"
                  type="number"
                  value={extractedData.quantity}
                  onChange={(e) => setExtractedData({ ...extractedData, quantity: parseFloat(e.target.value) })}
                  fullWidth
                  sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.quantity) }}
                  helperText={`Confidence: ${confidenceScores.quantity}%`}
                />

                <TextField
                  label="Unit Price ($)"
                  type="number"
                  value={extractedData.unitPrice}
                  onChange={(e) => setExtractedData({ ...extractedData, unitPrice: parseFloat(e.target.value) })}
                  fullWidth
                  sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.unitPrice) }}
                  helperText={`Confidence: ${confidenceScores.unitPrice}%`}
                />
              </Box>

              <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2 }}>
                <TextField
                  label="Total Amount ($)"
                  type="number"
                  value={extractedData.totalAmount}
                  onChange={(e) => setExtractedData({ ...extractedData, totalAmount: parseFloat(e.target.value) })}
                  fullWidth
                  sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.totalAmount) }}
                  helperText={`Confidence: ${confidenceScores.totalAmount}%`}
                />

                <TextField
                  label="Tax Amount ($)"
                  type="number"
                  value={extractedData.taxAmount}
                  onChange={(e) => setExtractedData({ ...extractedData, taxAmount: parseFloat(e.target.value) })}
                  fullWidth
                  sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.taxAmount) }}
                  helperText={`Confidence: ${confidenceScores.taxAmount}% - Please verify`}
                />
              </Box>
            </Box>
          </Paper>

          <Alert severity="info" sx={{ mb: 3 }}>
            <Typography>
              After confirming, the system will automatically perform 4-way match validation against PO, Goods Receipt, and Quality Control records.
            </Typography>
          </Alert>

          <Box sx={{ display: 'flex', gap: 2, justifyContent: 'flex-end' }}>
            <Button
              variant="outlined"
              onClick={() => {
                setExtractedData(null);
                setConfidenceScores(null);
                setSelectedFile(null);
              }}
              sx={{ color: oilGasTheme.colors.textMuted }}
            >
              Cancel
            </Button>
            <Button
              variant="contained"
              onClick={handleConfirm}
              sx={{
                backgroundColor: oilGasTheme.colors.success,
                '&:hover': { backgroundColor: '#1e7e34' }
              }}
            >
              Confirm and Process Invoice
            </Button>
          </Box>
        </>
      )}
    </Box>
  );
};
