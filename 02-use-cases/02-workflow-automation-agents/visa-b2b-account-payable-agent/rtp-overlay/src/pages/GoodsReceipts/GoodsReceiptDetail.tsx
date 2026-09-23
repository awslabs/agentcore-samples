import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Box, Button, Typography, Paper, Alert, CircularProgress } from '@mui/material';
import { ArrowBack, CheckCircle, Warning } from '@mui/icons-material';
import { oilGasTheme } from '../../styles/oilGasTheme';
import MatchStatusBadge from '../../components/common/MatchStatusBadge';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'https://56hm3anw06.execute-api.us-east-1.amazonaws.com/prod';

interface GoodsReceiptData {
  id: string;
  grNumber: string;
  poNumber: string;
  vendorName: string;
  receivedDate: string;
  receivedBy: string;
  createdAt: string;
  materialDescription: string;
  receivedQuantity: number;
  unit: string;
  matchStatus: 'MATCHED' | 'MISMATCHED' | 'PENDING';
  documentUrl?: string;
  confidenceScores?: Record<string, number>;
  notes?: string;
  purchaseOrder?: {
    quantity: number;
  };
}

export const GoodsReceiptDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [goodsReceipt, setGoodsReceipt] = useState<GoodsReceiptData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchGoodsReceipt();
  }, [id]);

  const fetchGoodsReceipt = async () => {
    try {
      setLoading(true);
      const token = localStorage.getItem('accessToken');
      const headers: HeadersInit = {
        'Content-Type': 'application/json',
      };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const response = await fetch(`${API_BASE_URL}/api/goods-receipts/${id}`, {
        headers,
      });

      if (!response.ok) {
        if (response.status === 404) {
          setError('Goods Receipt not found');
          return;
        }
        throw new Error(`Failed to fetch goods receipt: ${response.statusText}`);
      }

      const gr = await response.json();

      // Transform backend data to component format
      // Calculate total PO quantity from items
      const poQuantity = gr.purchaseOrder?.items?.reduce((sum: number, item: any) => {
        return sum + (parseFloat(item.quantity) || 0);
      }, 0) || 0;

      const transformedGR: GoodsReceiptData = {
        id: gr.id,
        grNumber: gr.deliveryReference || `GR-${gr.id.slice(-6)}`,
        poNumber: gr.purchaseOrder?.poNumber || 'N/A',
        vendorName: gr.purchaseOrder?.vendor?.name || 'Unknown Vendor',
        receivedDate: gr.deliveryDate,
        receivedBy: gr.receivedBy?.name || gr.receivedBy?.username || 'Unknown',
        createdAt: gr.createdAt || gr.deliveryDate,
        materialDescription: gr.conditionNotes || 'N/A',
        receivedQuantity: parseFloat(gr.quantityReceived),
        unit: 'units',
        matchStatus: gr.status === 'qc_passed' ? 'MATCHED' : gr.status === 'qc_failed' ? 'MISMATCHED' : 'PENDING',
        documentUrl: gr.sourceFileKey,
        confidenceScores: gr.confidenceScores,
        notes: gr.conditionNotes,
        purchaseOrder: gr.purchaseOrder ? {
          quantity: poQuantity
        } : undefined,
      };

      setGoodsReceipt(transformedGR);
      setError(null);
    } catch (err) {
      console.error('Error fetching goods receipt:', err);
      setError(err instanceof Error ? err.message : 'Failed to load goods receipt');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ p: 3, display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '400px' }}>
        <CircularProgress />
      </Box>
    );
  }

  if (error || !goodsReceipt) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error">{error || 'Goods Receipt not found'}</Alert>
        <Button onClick={() => navigate('/goods-receipts')} sx={{ mt: 2 }}>
          Back to List
        </Button>
      </Box>
    );
  }

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  };

  const quantityVariance = goodsReceipt.purchaseOrder && goodsReceipt.purchaseOrder.quantity > 0
    ? ((goodsReceipt.receivedQuantity - goodsReceipt.purchaseOrder.quantity) / goodsReceipt.purchaseOrder.quantity) * 100
    : 0;

  return (
    <Box sx={{ p: 3 }}>
      {/* Header */}
      <Box sx={{ mb: 3, display: 'flex', alignItems: 'center', gap: 2 }}>
        <Button
          startIcon={<ArrowBack />}
          onClick={() => navigate('/goods-receipts')}
          sx={{ color: oilGasTheme.colors.textMuted }}
        >
          Back
        </Button>
        <Box sx={{ flex: 1 }}>
          <Typography variant="h4" sx={{ color: oilGasTheme.colors.primary }}>
            {goodsReceipt.grNumber}
          </Typography>
          <Typography variant="body2" color="textSecondary">
            Goods Receipt Details
          </Typography>
        </Box>
        <MatchStatusBadge status={goodsReceipt.matchStatus} />
      </Box>

      {/* GR Header Information */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Receipt Information
        </Typography>
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 3 }}>
          <Box>
            <Typography variant="caption" color="textSecondary">
              GR Number
            </Typography>
            <Typography variant="body1" sx={{ fontWeight: 'bold' }}>
              {goodsReceipt.grNumber}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="textSecondary">
              PO Number
            </Typography>
            <Typography variant="body1" sx={{ fontWeight: 'bold' }}>
              {goodsReceipt.poNumber}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="textSecondary">
              Vendor
            </Typography>
            <Typography variant="body1">{goodsReceipt.vendorName}</Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="textSecondary">
              Received Date
            </Typography>
            <Typography variant="body1">{formatDate(goodsReceipt.receivedDate)}</Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="textSecondary">
              Received By
            </Typography>
            <Typography variant="body1">{goodsReceipt.receivedBy}</Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="textSecondary">
              Created
            </Typography>
            <Typography variant="body1">{formatDate(goodsReceipt.createdAt)}</Typography>
          </Box>
        </Box>
      </Paper>

      {/* Material Details */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Material Details
        </Typography>
        <Box sx={{ mb: 2 }}>
          <Typography variant="caption" color="textSecondary">
            Material Description
          </Typography>
          <Typography variant="body1" sx={{ fontWeight: 'bold' }}>
            {goodsReceipt.materialDescription}
          </Typography>
        </Box>
      </Paper>

      {/* Quantity Comparison */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Quantity Comparison
        </Typography>
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 3 }}>
          <Box>
            <Typography variant="caption" color="textSecondary">
              PO Quantity
            </Typography>
            <Typography variant="h5" sx={{ color: oilGasTheme.colors.textMuted }}>
              {goodsReceipt.purchaseOrder?.quantity.toLocaleString() || 'N/A'} {goodsReceipt.unit}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="textSecondary">
              Received Quantity
            </Typography>
            <Typography variant="h5" sx={{ color: oilGasTheme.colors.primary, fontWeight: 'bold' }}>
              {goodsReceipt.receivedQuantity.toLocaleString()} {goodsReceipt.unit}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="textSecondary">
              Variance
            </Typography>
            <Typography
              variant="h5"
              sx={{
                color:
                  Math.abs(quantityVariance) <= 2
                    ? oilGasTheme.colors.success
                    : Math.abs(quantityVariance) <= 5
                    ? oilGasTheme.colors.warning
                    : oilGasTheme.colors.danger,
                fontWeight: 'bold',
              }}
            >
              {quantityVariance > 0 ? '+' : ''}
              {quantityVariance.toFixed(2)}%
            </Typography>
          </Box>
        </Box>

        {Math.abs(quantityVariance) > 2 && (
          <Alert
            severity={Math.abs(quantityVariance) <= 5 ? 'warning' : 'error'}
            icon={Math.abs(quantityVariance) <= 5 ? <Warning /> : undefined}
            sx={{ mt: 2 }}
          >
            {Math.abs(quantityVariance) <= 5
              ? 'Quantity variance is within acceptable tolerance (≤5%)'
              : 'Quantity variance exceeds acceptable tolerance (>5%)'}
          </Alert>
        )}
        {Math.abs(quantityVariance) <= 2 && (
          <Alert severity="success" icon={<CheckCircle />} sx={{ mt: 2 }}>
            Quantity matches PO within normal tolerance
          </Alert>
        )}
      </Paper>

      {/* Document Information */}
      {goodsReceipt.documentUrl && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="h6" sx={{ mb: 2 }}>
            Document Information
          </Typography>
          <Box sx={{ mb: 2 }}>
            <Typography variant="body2" color="textSecondary" sx={{ mb: 1 }}>
              A document was uploaded for this goods receipt
            </Typography>
            <Button variant="outlined" sx={{ color: oilGasTheme.colors.primary }}>
              Download Document
            </Button>
          </Box>

          {goodsReceipt.confidenceScores && (
            <>
              <Typography variant="subtitle2" sx={{ mt: 3, mb: 1 }}>
                AI Extraction Confidence Scores
              </Typography>
              <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 2 }}>
                {Object.entries(goodsReceipt.confidenceScores).map(([field, score]) => (
                  <Box key={field}>
                    <Typography variant="caption" color="textSecondary">
                      {field.replace(/([A-Z])/g, ' $1').trim()}
                    </Typography>
                    <Typography
                      variant="body2"
                      sx={{
                        color:
                          score >= 85
                            ? oilGasTheme.colors.success
                            : score >= 70
                            ? oilGasTheme.colors.warning
                            : oilGasTheme.colors.danger,
                        fontWeight: 'bold',
                      }}
                    >
                      {score}%
                    </Typography>
                  </Box>
                ))}
              </Box>
            </>
          )}
        </Paper>
      )}

      {/* Notes */}
      {goodsReceipt.notes && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="h6" sx={{ mb: 2 }}>
            Notes
          </Typography>
          <Typography variant="body1">{goodsReceipt.notes}</Typography>
        </Paper>
      )}

      {/* Match Status */}
      <Paper sx={{ p: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Match Status
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <MatchStatusBadge status={goodsReceipt.matchStatus} />
          <Typography variant="body2" color="textSecondary">
            {goodsReceipt.matchStatus === 'MATCHED' &&
              'This goods receipt has been matched with PO and Invoice'}
            {goodsReceipt.matchStatus === 'PENDING' &&
              'Waiting for invoice or quality control validation'}
            {goodsReceipt.matchStatus === 'MISMATCHED' &&
              'Discrepancies found during validation'}
          </Typography>
        </Box>
      </Paper>
    </Box>
  );
};
