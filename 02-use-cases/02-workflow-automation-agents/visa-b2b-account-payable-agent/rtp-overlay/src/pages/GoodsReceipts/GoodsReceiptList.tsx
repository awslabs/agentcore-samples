/**
 * Goods Receipt List Page
 */

import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import MatchStatusBadge from '../../components/common/MatchStatusBadge';
import { oilGasTheme } from '../../styles/oilGasTheme';
import * as apiService from '../../services/api.service';

interface GoodsReceipt {
  id: string;
  grNumber: string;
  poNumber: string;
  vendorName: string;
  materialDescription: string;
  receivedQuantity: number;
  unit: string;
  receivedDate: string;
  matchStatus: 'MATCHED' | 'MISMATCHED' | 'PENDING';
}

const GoodsReceiptList: React.FC = () => {
  const navigate = useNavigate();
  const [goodsReceipts, setGoodsReceipts] = useState<GoodsReceipt[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchGoodsReceipts();
  }, []);

  const fetchGoodsReceipts = async () => {
    try {
      setLoading(true);
      const receipts = await apiService.getGoodsReceipts();

      // Transform to component format
      const transformedGRs: GoodsReceipt[] = receipts.map((gr: any) => {
        let matchStatus: 'MATCHED' | 'MISMATCHED' | 'PENDING' = 'PENDING';
        if (gr.status === 'qc_passed') {
          matchStatus = 'MATCHED';
        } else if (gr.status === 'qc_failed') {
          matchStatus = 'MISMATCHED';
        }

        return {
          id: gr.id,
          grNumber: gr.purchaseOrder?.poNumber ? `GR-${gr.id.slice(-6)}` : gr.deliveryReference || `GR-${gr.id.slice(-6)}`,
          poNumber: gr.purchaseOrder?.poNumber || 'N/A',
          vendorName: gr.purchaseOrder?.vendor?.name || 'Unknown Vendor',
          materialDescription: gr.conditionNotes || 'N/A',
          receivedQuantity: gr.quantityReceived,
          unit: 'units',
          receivedDate: gr.deliveryDate instanceof Date ? gr.deliveryDate.toISOString() : gr.deliveryDate,
          matchStatus,
        };
      });

      setGoodsReceipts(transformedGRs);
      setError(null);
    } catch (err) {
      console.error('Error fetching goods receipts:', err);
      setError(err instanceof Error ? err.message : 'Failed to load goods receipts');
      setGoodsReceipts([]);
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  };

  if (loading) {
    return (
      <div style={{ padding: oilGasTheme.spacing.xl, textAlign: 'center' }}>
        <p style={{ fontSize: oilGasTheme.typography.fontSize.lg, color: oilGasTheme.colors.textMuted }}>
          Loading goods receipts...
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: oilGasTheme.spacing.xl }}>
        <div style={{ backgroundColor: oilGasTheme.colors.danger + '20', padding: oilGasTheme.spacing.lg, borderRadius: oilGasTheme.borderRadius.md, marginBottom: oilGasTheme.spacing.lg }}>
          <p style={{ color: oilGasTheme.colors.danger, margin: 0 }}>
            Error: {error}
          </p>
        </div>
        <button
          onClick={fetchGoodsReceipts}
          style={{ padding: `${oilGasTheme.spacing.md} ${oilGasTheme.spacing.lg}`, backgroundColor: oilGasTheme.colors.primary, color: '#ffffff', border: 'none', borderRadius: oilGasTheme.borderRadius.md, cursor: 'pointer' }}
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div>
      <div style={{ marginBottom: oilGasTheme.spacing.xl, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: oilGasTheme.typography.fontSize['3xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.text, margin: 0, marginBottom: oilGasTheme.spacing.xs }}>
            Goods Receipts
          </h1>
          <p style={{ fontSize: oilGasTheme.typography.fontSize.base, color: oilGasTheme.colors.textMuted, margin: 0 }}>
            {goodsReceipts.length === 0 ? 'No goods receipts yet. Create or upload your first GR to get started.' : 'Record and manage delivery acceptances'}
          </p>
        </div>
        <div style={{ display: 'flex', gap: oilGasTheme.spacing.md }}>
          <button
            onClick={() => navigate('/goods-receipts/upload')}
            style={{ padding: `${oilGasTheme.spacing.md} ${oilGasTheme.spacing.lg}`, backgroundColor: oilGasTheme.colors.primary, color: '#ffffff', border: 'none', borderRadius: oilGasTheme.borderRadius.md, fontSize: oilGasTheme.typography.fontSize.base, fontWeight: oilGasTheme.typography.fontWeight.semibold, cursor: 'pointer' }}
          >
            📄 Upload BOL Document
          </button>
        </div>
      </div>

      {goodsReceipts.length === 0 ? (
        <div style={{ backgroundColor: oilGasTheme.colors.surface, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm, padding: oilGasTheme.spacing.xl, textAlign: 'center' }}>
          <p style={{ fontSize: oilGasTheme.typography.fontSize.lg, color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.md }}>
            No goods receipts found
          </p>
          <p style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted }}>
            Create a manual GR or upload a BOL document to get started
          </p>
        </div>
      ) : (
        <div style={{ backgroundColor: oilGasTheme.colors.surface, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ backgroundColor: oilGasTheme.colors.background, borderBottom: `2px solid ${oilGasTheme.colors.border}` }}>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>GR Number</th>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>PO Number</th>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Vendor</th>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Material</th>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Quantity</th>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Date</th>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Match Status</th>
              </tr>
            </thead>
            <tbody>
              {goodsReceipts.map((gr, index) => (
              <tr
                key={gr.id}
                onClick={() => navigate(`/goods-receipts/${gr.id}`)}
                style={{ borderBottom: `1px solid ${oilGasTheme.colors.border}`, backgroundColor: index % 2 === 0 ? oilGasTheme.colors.surface : oilGasTheme.colors.background, cursor: 'pointer' }}
              >
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.primary, fontWeight: oilGasTheme.typography.fontWeight.semibold }}>{gr.grNumber}</td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.text }}>{gr.poNumber}</td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.text }}>{gr.vendorName}</td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.text }}>{gr.materialDescription}</td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.text }}>{gr.receivedQuantity.toLocaleString()} {gr.unit}</td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.text }}>{formatDate(gr.receivedDate)}</td>
                <td style={{ padding: oilGasTheme.spacing.md }}><MatchStatusBadge status={gr.matchStatus} size="sm" /></td>
              </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default GoodsReceiptList;
