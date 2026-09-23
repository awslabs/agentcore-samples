import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Button, Typography, Paper, Alert, CircularProgress, TextField } from '@mui/material';
import { CloudUpload, CheckCircle, Warning, Error as ErrorIcon } from '@mui/icons-material';
import { oilGasTheme } from '../../styles/oilGasTheme';
import { documentService } from '../../services/documentService';

interface ExtractedData {
  poNumber: string;
  vendor: string;
  material: string;
  quantity: number;
  deliveryDate: string;
  bolNumber: string;
  receivedBy: string;
}

interface ConfidenceScores {
  poNumber: number;
  vendor: number;
  material: number;
  quantity: number;
  deliveryDate: number;
  bolNumber: number;
  receivedBy: number;
}

export const UploadGoodsReceipt: React.FC = () => {
  const navigate = useNavigate();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractedData, setExtractedData] = useState<ExtractedData | null>(null);
  const [confidenceScores, setConfidenceScores] = useState<ConfidenceScores | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [grId, setGrId] = useState<string | null>(null);

  // Real AI extraction using documentService
  const performExtraction = async (file: File) => {
    setIsUploading(true);
    setIsExtracting(false);
    setError(null);

    try {
      // Step 1: Upload file
      const uploadResult = await documentService.uploadGoodsReceipt(file);
      
      setIsUploading(false);
      setIsExtracting(true);

      // Step 2: Poll for completion
      const jobStatus = await documentService.pollForJobCompletion(uploadResult.jobId);

      // Step 3: Get GR data
      if (!jobStatus.resultId) {
        throw new Error('Job completed but no GR ID returned');
      }

      const gr = await documentService.getGoodsReceipt(jobStatus.resultId);
      setGrId(gr.id);

      // Transform backend data to component format
      const extracted: ExtractedData = {
        poNumber: gr.purchaseOrder?.poNumber || gr.extractedData?.po_reference || '',
        vendor: gr.vendor?.name || gr.extractedData?.vendor_name || 'Unknown Vendor',
        material: gr.materialDescription || gr.extractedData?.material_description || '',
        quantity: gr.receivedQuantity,
        deliveryDate: gr.receivedDate ? gr.receivedDate.split('T')[0] : new Date().toISOString().split('T')[0],
        bolNumber: gr.bolNumber || gr.extractedData?.bol_number || '',
        receivedBy: gr.receivedBy || ''
      };

      // Extract confidence scores if available
      const confidence: ConfidenceScores = {
        poNumber: gr.confidenceScores?.po_reference || 85,
        vendor: gr.confidenceScores?.vendor_name || 90,
        material: gr.confidenceScores?.material_description || 85,
        quantity: gr.confidenceScores?.quantity_received || 80,
        deliveryDate: gr.confidenceScores?.delivery_date || 90,
        bolNumber: gr.confidenceScores?.bol_number || 75,
        receivedBy: gr.confidenceScores?.received_by || 80
      };

      setExtractedData(extracted);
      setConfidenceScores(confidence);
      setIsExtracting(false);
    } catch (err) {
      setIsUploading(false);
      setIsExtracting(false);
      setError(err instanceof Error ? err.message : 'Failed to process goods receipt');
      console.error('GR extraction error:', err);
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
    if (grId) {
      // GR already created and extracted, just navigate to view it
      navigate(`/goods-receipts/${grId}`);
    } else {
      alert('Goods Receipt processed successfully!');
      navigate('/goods-receipts');
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
        Upload Bill of Lading (BOL)
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
              Drag and drop your document here
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
                <Typography>Uploading document to S3...</Typography>
              </Box>
            </Alert>
          )}

          {isExtracting && (
            <Alert severity="info" sx={{ mb: 3 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <CircularProgress size={20} />
                <Typography>Processing document with AI extraction... This may take up to 30 seconds.</Typography>
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
              <Typography>Document extracted successfully! Please review and edit the data below.</Typography>
            </Box>
          </Alert>

          <Alert severity="warning" sx={{ mb: 3 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Warning />
              <Typography>Fields highlighted in yellow have lower confidence scores and should be verified.</Typography>
            </Box>
          </Alert>

          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h6" sx={{ mb: 3 }}>Extracted Data - Review and Confirm</Typography>
            
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <TextField
                label="PO Number"
                value={extractedData.poNumber}
                onChange={(e) => setExtractedData({ ...extractedData, poNumber: e.target.value })}
                fullWidth
                sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.poNumber) }}
                helperText={`Confidence: ${confidenceScores.poNumber}%`}
              />

              <TextField
                label="Vendor"
                value={extractedData.vendor}
                onChange={(e) => setExtractedData({ ...extractedData, vendor: e.target.value })}
                fullWidth
                sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.vendor) }}
                helperText={`Confidence: ${confidenceScores.vendor}%`}
              />

              <TextField
                label="Material"
                value={extractedData.material}
                onChange={(e) => setExtractedData({ ...extractedData, material: e.target.value })}
                fullWidth
                sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.material) }}
                helperText={`Confidence: ${confidenceScores.material}%`}
              />

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
                label="Delivery Date"
                type="date"
                value={extractedData.deliveryDate}
                onChange={(e) => setExtractedData({ ...extractedData, deliveryDate: e.target.value })}
                fullWidth
                InputLabelProps={{ shrink: true }}
                sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.deliveryDate) }}
                helperText={`Confidence: ${confidenceScores.deliveryDate}%`}
              />

              <TextField
                label="BOL Number"
                value={extractedData.bolNumber}
                onChange={(e) => setExtractedData({ ...extractedData, bolNumber: e.target.value })}
                fullWidth
                sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.bolNumber) }}
                helperText={`Confidence: ${confidenceScores.bolNumber}% - Please verify`}
              />

              <TextField
                label="Received By"
                value={extractedData.receivedBy}
                onChange={(e) => setExtractedData({ ...extractedData, receivedBy: e.target.value })}
                fullWidth
                sx={{ backgroundColor: getConfidenceBgColor(confidenceScores.receivedBy) }}
                helperText={`Confidence: ${confidenceScores.receivedBy}%`}
              />
            </Box>
          </Paper>

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
              Confirm and Create GR
            </Button>
          </Box>
        </>
      )}
    </Box>
  );
};
