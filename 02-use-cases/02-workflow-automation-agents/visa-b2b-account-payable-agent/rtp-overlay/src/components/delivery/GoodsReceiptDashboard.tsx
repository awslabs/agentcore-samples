import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box } from '@mui/material';
import ShoppingCartIcon from '@mui/icons-material/ShoppingCart';
import LocalShippingIcon from '@mui/icons-material/LocalShipping';
import WarningIcon from '@mui/icons-material/Warning';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import { PageHeader } from '../layout/PageHeader';
import { QBMetricCard } from '../common/QBMetricCard';
import { QBCard } from '../common/QBCard';
import { QBTable } from '../common/QBTable';
import type { QBTableColumn } from '../common/QBTable';
import { QBBadge } from '../common/QBBadge';
import * as apiService from '../../services/api.service';
import type { GoodsReceipt } from '../../types/data.types';

export const GoodsReceiptDashboard: React.FC = () => {
  const navigate = useNavigate();
  const [goodsReceipts, setGoodsReceipts] = useState<GoodsReceipt[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadDashboardData();
  }, []);

  const loadDashboardData = async () => {
    try {
      setError(null);
      const data = await apiService.getGoodsReceipts();
      setGoodsReceipts(data);
    } catch (error) {
      console.error('Failed to load goods receipts:', error);
      setError('Failed to load goods receipts. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  // Calculate metrics
  const totalReceipts = goodsReceipts.length;
  const pendingQC = goodsReceipts.filter((gr) => gr.status === 'pending_qc').length;
  const qcPassed = goodsReceipts.filter((gr) => gr.status === 'qc_passed').length;
  const qcFailed = goodsReceipts.filter((gr) => gr.status === 'qc_failed').length;

  // Show all receipts (not just recent 5)
  const displayedReceipts = goodsReceipts;

  const columns: QBTableColumn<GoodsReceipt>[] = [
    {
      id: 'id',
      label: 'Receipt ID',
      minWidth: 130,
      format: (value) => `GR-${value.slice(-6)}`,
    },
    {
      id: 'purchaseOrder.poNumber' as any,
      label: 'PO Number',
      minWidth: 130,
      format: (_value, row) => row.purchaseOrder?.poNumber || 'N/A',
    },
    {
      id: 'purchaseOrder.vendor.name' as any,
      label: 'Vendor',
      minWidth: 150,
      format: (_value, row) => row.purchaseOrder?.vendor?.name || 'N/A',
    },
    {
      id: 'deliveryDate',
      label: 'Delivery Date',
      minWidth: 120,
      format: (value) => new Date(value).toLocaleDateString(),
    },
    {
      id: 'quantityReceived',
      label: 'Quantity',
      minWidth: 100,
      align: 'right',
    },
    {
      id: 'status',
      label: 'Status',
      minWidth: 120,
      format: (value) => (
        <QBBadge
          label={value.replace('_', ' ').toUpperCase()}
          color={
            value === 'qc_passed'
              ? 'success'
              : value === 'pending_qc'
              ? 'warning'
              : 'error'
          }
        />
      ),
    },
    {
      id: 'deliveryReference',
      label: 'Reference',
      minWidth: 120,
    },
  ];

  if (loading) {
    return <Box>Loading...</Box>;
  }

  if (error) {
    return (
      <Box>
        <PageHeader title="Goods Receipt Dashboard" subtitle="Monitor delivery operations" />
        <Box sx={{ p: 3, textAlign: 'center', color: 'error.main' }}>
          {error}
        </Box>
      </Box>
    );
  }

  return (
    <Box>
      <PageHeader title="Goods Receipt Dashboard" subtitle="Monitor delivery operations and goods receipts" />

      {/* Metric Cards */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', md: 'repeat(4, 1fr)' },
          gap: 3,
          mb: 4,
        }}
      >
        <QBMetricCard
          title="Total Goods Receipts"
          value={totalReceipts}
          icon={<LocalShippingIcon />}
        />
        <QBMetricCard
          title="Pending QC"
          value={pendingQC}
          icon={<WarningIcon />}
        />
        <QBMetricCard
          title="QC Passed"
          value={qcPassed}
          icon={<CheckCircleIcon />}
        />
        <QBMetricCard
          title="QC Failed"
          value={qcFailed}
          icon={<ShoppingCartIcon />}
        />
      </Box>

      {/* All Goods Receipts */}
      <QBCard title="All Goods Receipts">
        {displayedReceipts.length === 0 ? (
          <Box sx={{ p: 3, textAlign: 'center', color: 'text.secondary' }}>
            No goods receipts found
          </Box>
        ) : (
          <QBTable
            columns={columns}
            data={displayedReceipts}
            onRowClick={() => navigate(`/delivery/purchase-orders`)}
          />
        )}
      </QBCard>
    </Box>
  );
};
