import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Alert, Snackbar, Card, CardContent, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ShoppingCartIcon from '@mui/icons-material/ShoppingCart';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import WarningIcon from '@mui/icons-material/Warning';
import AttachMoneyIcon from '@mui/icons-material/AttachMoney';
import { QBTable } from '../common/QBTable';
import type { QBTableColumn } from '../common/QBTable';
import { QBButton } from '../common/QBButton';
import { QBBadge } from '../common/QBBadge';
import { LoadingSpinner } from '../common/LoadingSpinner';
import { PageHeader } from '../layout/PageHeader';
import * as apiService from '../../services/api.service';
import type { PurchaseOrder, POFilters, Vendor } from '../../types/data.types';
import { POFilterPanel } from './POFilterPanel';
import { POActionMenu } from './POActionMenu';
import { CreatePODialog } from './CreatePODialog';
import { UploadReceiptDialog } from './UploadReceiptDialog';
import { UploadInvoiceDialog } from './UploadInvoiceDialog';
import {
  TotalRemainingCell,
  FulfillmentCell,
  MatchStatusCell,
  DueDateCell,
  AttachmentsCell,
} from './cells';

export const PurchaseOrderList: React.FC = () => {
  const navigate = useNavigate();
  const [orders, setOrders] = useState<PurchaseOrder[]>([]);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState<POFilters>({
    status: [],
    vendorId: null,
    warehouseId: null,
    dateRange: { start: null, end: null },
    overdueOnly: false,
    exceptionsOnly: false,
    unmatchedInvoicesOnly: false,
  });

  // Dialog states
  const [createPOOpen, setCreatePOOpen] = useState(false);
  const [uploadReceiptOpen, setUploadReceiptOpen] = useState(false);
  const [uploadInvoiceOpen, setUploadInvoiceOpen] = useState(false);
  const [selectedPO, setSelectedPO] = useState<PurchaseOrder | null>(null);

  // Notification state
  const [notification, setNotification] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error' | 'info';
  }>({ open: false, message: '', severity: 'success' });

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    loadOrders();
  }, [filters]);

  const loadData = async () => {
    try {
      setLoading(true);
      const [ordersData, vendorsData] = await Promise.all([
        apiService.getPurchaseOrdersEnhanced(filters),
        apiService.getVendors(),
      ]);
      setOrders(ordersData);
      setVendors(vendorsData);
    } catch (error) {
      console.error('Failed to load data:', error);
      showNotification('Failed to load data', 'error');
    } finally {
      setLoading(false);
    }
  };

  const loadOrders = async () => {
    try {
      // Fetch all POs (backend doesn't support filtering yet)
      const allData = await apiService.getPurchaseOrdersEnhanced({});
      
      // Apply client-side filtering
      let filteredData = allData;
      
      // Filter by status
      if (filters.status && filters.status.length > 0) {
        filteredData = filteredData.filter(po => filters.status.includes(po.status));
      }
      
      // Filter by vendor
      if (filters.vendorId) {
        filteredData = filteredData.filter(po => po.vendor.id === filters.vendorId);
      }
      
      // Filter by warehouse
      if (filters.warehouseId) {
        filteredData = filteredData.filter(po => po.warehouse === filters.warehouseId);
      }
      
      // Filter overdue only
      if (filters.overdueOnly) {
        filteredData = filteredData.filter(po => po.isOverdue);
      }
      
      // Filter exceptions only
      if (filters.exceptionsOnly) {
        filteredData = filteredData.filter(po => po.status === 'exception');
      }
      
      // Filter unmatched invoices only
      if (filters.unmatchedInvoicesOnly) {
        filteredData = filteredData.filter(po => po.hasInvoice && po.variancePercentage > 0);
      }
      
      // Filter by date range
      if (filters.dateRange.start || filters.dateRange.end) {
        filteredData = filteredData.filter(po => {
          const poDate = new Date(po.assignedDate);
          if (filters.dateRange.start && poDate < new Date(filters.dateRange.start)) {
            return false;
          }
          if (filters.dateRange.end && poDate > new Date(filters.dateRange.end)) {
            return false;
          }
          return true;
        });
      }
      
      setOrders(filteredData);
    } catch (error) {
      console.error('Failed to load purchase orders:', error);
    }
  };

  const showNotification = (message: string, severity: 'success' | 'error' | 'info' = 'success') => {
    setNotification({ open: true, message, severity });
  };

  const handleStartReceipt = (order: PurchaseOrder) => {
    navigate(`/delivery/capture/${order.id}`);
  };

  const handleContinueReceipt = (order: PurchaseOrder) => {
    navigate(`/delivery/capture/${order.id}`);
  };

  const handleFilterChange = (newFilters: POFilters) => {
    setFilters(newFilters);
  };

  const handleClearFilters = () => {
    setFilters({
      status: [],
      vendorId: null,
      warehouseId: null,
      dateRange: { start: null, end: null },
      overdueOnly: false,
      exceptionsOnly: false,
      unmatchedInvoicesOnly: false,
    });
  };

  // Dialog handlers
  const handleCreatePO = async (poData: any) => {
    try {
      await apiService.createPurchaseOrder(poData);
      showNotification('Purchase order created successfully');
      loadOrders();
    } catch (error) {
      showNotification('Failed to create purchase order', 'error');
      throw error;
    }
  };

  const handleUploadReceipt = (po: PurchaseOrder) => {
    setSelectedPO(po);
    setUploadReceiptOpen(true);
  };

  const handleUploadInvoice = (po: PurchaseOrder) => {
    setSelectedPO(po);
    setUploadInvoiceOpen(true);
  };

  const handleViewPO = (po: PurchaseOrder) => {
    // Navigate to PO detail view or show detail dialog
    console.log('View PO:', po);
    showNotification('PO detail view coming soon', 'info');
  };

  const handleViewMatches = (po: PurchaseOrder) => {
    // TODO: Implement when ViewMatchesDialog is ready
    console.log('View matches for PO:', po);
    showNotification('View matches feature coming soon', 'info');
  };

  const handleClosePO = (po: PurchaseOrder) => {
    // TODO: Implement when ClosePODialog is ready
    console.log('Close PO:', po);
    showNotification('Close PO feature coming soon', 'info');
  };

  const handleOverrideTolerance = (po: PurchaseOrder) => {
    // TODO: Implement override tolerance
    console.log('Override tolerance for PO:', po);
    showNotification('Override tolerance feature coming soon', 'info');
  };

  const handleReceiptUploadSuccess = (_receiptId: string) => {
    showNotification('Receipt uploaded successfully');
    loadOrders();
  };

  const handleInvoiceUploadSuccess = (_invoiceId: string) => {
    showNotification('Invoice uploaded successfully');
    loadOrders();
  };

  const getStatusVariant = (status: string): 'success' | 'warning' | 'error' | 'info' | 'default' => {
    switch (status) {
      case 'active':
        return 'success';
      case 'exception':
        return 'error';
      case 'partially-received':
        return 'warning';
      case 'received':
      case 'invoiced':
        return 'info';
      case 'closed':
        return 'default';
      default:
        return 'default';
    }
  };

  const columns: QBTableColumn<PurchaseOrder>[] = [
    {
      id: 'poNumber',
      label: 'PO Number',
      minWidth: 130,
    },
    {
      id: 'vendor',
      label: 'Vendor',
      minWidth: 180,
      format: (value) => value.name,
    },
    {
      id: 'dueDate',
      label: 'Due Date',
      minWidth: 130,
      format: (_value, row) => (
        <DueDateCell dueDate={row.dueDate} isOverdue={row.isOverdue} />
      ),
    },
    {
      id: 'totalAmount',
      label: 'Total / Remaining',
      minWidth: 160,
      format: (_value, row) => (
        <TotalRemainingCell
          totalAmount={row.totalAmount}
          remainingAmount={row.remainingAmount}
          currency={row.currency}
        />
      ),
    },
    {
      id: 'fulfillment',
      label: 'Fulfillment',
      minWidth: 150,
      format: (_value, row) => (
        <FulfillmentCell
          orderedQuantity={row.orderedQuantity}
          receivedQuantity={row.receivedQuantity}
          fulfillmentPercentage={row.fulfillmentPercentage}
        />
      ),
    },
    {
      id: 'matchStatus',
      label: 'Match Status',
      minWidth: 120,
      format: (_value, row) => <MatchStatusCell matchStatus={row.matchStatus} />,
    },
    {
      id: 'attachments',
      label: 'Docs',
      minWidth: 80,
      align: 'center',
      format: (_value, row) => (
        <AttachmentsCell
          attachments={row.attachments}
          onViewAttachments={() => handleViewMatches(row)}
        />
      ),
    },
    {
      id: 'status',
      label: 'Status',
      minWidth: 120,
      format: (value) => (
        <QBBadge
          label={value.replace('-', ' ').toUpperCase()}
          color={getStatusVariant(value)}
        />
      ),
    },
    {
      id: 'actions',
      label: 'Actions',
      minWidth: 180,
      align: 'center',
      format: (_value, row) => (
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', justifyContent: 'center' }}>
          {!row.hasReceipt && row.status !== 'closed' && (
            <QBButton variant="primary" size="small" onClick={() => handleStartReceipt(row)}>
              Start Receipt
            </QBButton>
          )}
          {row.hasPartialReceipt && row.status !== 'closed' && (
            <QBButton variant="secondary" size="small" onClick={() => handleContinueReceipt(row)}>
              Continue Receipt
            </QBButton>
          )}
          <POActionMenu
            purchaseOrder={row}
            currentUserRole="admin"
            onUploadReceipt={handleUploadReceipt}
            onUploadInvoice={handleUploadInvoice}
            onViewPO={handleViewPO}
            onViewMatches={handleViewMatches}
            onClosePO={handleClosePO}
            onOverrideTolerance={handleOverrideTolerance}
          />
        </Box>
      ),
    },
  ];

  const warehouses = Array.from(new Set(orders.map((o) => o.warehouse).filter(Boolean))) as string[];

  // Calculate metrics
  const metrics = {
    total: orders.length,
    active: orders.filter(o => o.status === 'active').length,
    overdue: orders.filter(o => o.isOverdue).length,
    totalValue: orders.reduce((sum, o) => sum + o.totalAmount, 0),
    avgFulfillment: orders.length > 0 
      ? Math.round(orders.reduce((sum, o) => sum + o.fulfillmentPercentage, 0) / orders.length)
      : 0,
  };

  if (loading) {
    return <LoadingSpinner message="Loading purchase orders..." />;
  }

  return (
    <Box>
      <PageHeader
          title="Purchase Orders"
          subtitle="Manage purchase orders, receipts, and invoices"
          actions={
            <Box sx={{ display: 'flex', gap: 2 }}>
              <QBButton
                variant="primary"
                onClick={() => setCreatePOOpen(true)}
                startIcon={<AddIcon />}
              >
                Create PO
              </QBButton>
              <QBButton variant="secondary" onClick={() => navigate('/delivery')}>
                Back to Dashboard
              </QBButton>
            </Box>
          }
        />

        {/* Metrics Cards */}
        <Box sx={{ display: 'flex', gap: 3, mb: 4, flexWrap: 'wrap' }}>
          <Card sx={{ flex: '1 1 calc(25% - 18px)', minWidth: '200px', height: '160px', background: 'linear-gradient(135deg, #2CA01C 0%, #1e7a13 100%)' }}>
            <CardContent sx={{ p: 3, height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <ShoppingCartIcon sx={{ fontSize: 48, color: 'white', opacity: 0.9 }} />
                <Typography variant="h3" sx={{ color: 'white', fontWeight: 700, fontSize: '2.5rem' }}>
                  {metrics.total}
                </Typography>
              </Box>
              <Typography variant="body1" sx={{ color: 'white', opacity: 0.95, fontWeight: 500 }}>
                Total Purchase Orders
              </Typography>
            </CardContent>
          </Card>

          <Card sx={{ flex: '1 1 calc(25% - 18px)', minWidth: '200px', height: '160px', background: 'linear-gradient(135deg, #4ade80 0%, #22c55e 100%)' }}>
            <CardContent sx={{ p: 3, height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <CheckCircleIcon sx={{ fontSize: 48, color: 'white', opacity: 0.9 }} />
                <Typography variant="h3" sx={{ color: 'white', fontWeight: 700, fontSize: '2.5rem' }}>
                  {metrics.active}
                </Typography>
              </Box>
              <Typography variant="body1" sx={{ color: 'white', opacity: 0.95, fontWeight: 500 }}>
                Active Orders
              </Typography>
            </CardContent>
          </Card>

          <Card sx={{ flex: '1 1 calc(25% - 18px)', minWidth: '200px', height: '160px', background: 'linear-gradient(135deg, #fbbf24 0%, #f59e0b 100%)' }}>
            <CardContent sx={{ p: 3, height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <WarningIcon sx={{ fontSize: 48, color: 'white', opacity: 0.9 }} />
                <Typography variant="h3" sx={{ color: 'white', fontWeight: 700, fontSize: '2.5rem' }}>
                  {metrics.overdue}
                </Typography>
              </Box>
              <Typography variant="body1" sx={{ color: 'white', opacity: 0.95, fontWeight: 500 }}>
                Overdue Orders
              </Typography>
            </CardContent>
          </Card>

          <Card sx={{ flex: '1 1 calc(25% - 18px)', minWidth: '200px', height: '160px', background: 'linear-gradient(135deg, #10b981 0%, #059669 100%)' }}>
            <CardContent sx={{ p: 3, height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <AttachMoneyIcon sx={{ fontSize: 48, color: 'white', opacity: 0.9 }} />
                <Typography variant="h3" sx={{ color: 'white', fontWeight: 700, fontSize: '2.5rem' }}>
                  ${(metrics.totalValue / 1000).toFixed(0)}K
                </Typography>
              </Box>
              <Box>
                <Typography variant="body1" sx={{ color: 'white', opacity: 0.95, fontWeight: 500 }}>
                  Total Value
                </Typography>
                <Typography variant="body2" sx={{ color: 'white', opacity: 0.8, mt: 0.5 }}>
                  Avg Fulfillment: {metrics.avgFulfillment}%
                </Typography>
              </Box>
            </CardContent>
          </Card>
        </Box>

        {/* Filter Panel */}
        <POFilterPanel
          filters={filters}
          vendors={vendors}
          warehouses={warehouses}
          onFilterChange={handleFilterChange}
          onClearFilters={handleClearFilters}
        />

        {/* Table */}
        {orders.length === 0 ? (
          <Box sx={{ textAlign: 'center', py: 8 }}>
            {filters.status.length > 0 || filters.vendorId || filters.overdueOnly
              ? 'No purchase orders found. Try adjusting your filters.'
              : 'No purchase orders found. Create your first purchase order to get started.'}
          </Box>
        ) : (
          <QBTable columns={columns} data={orders} />
        )}

      {/* Dialogs */}
      <CreatePODialog
        open={createPOOpen}
        vendors={vendors}
        onClose={() => setCreatePOOpen(false)}
        onSubmit={handleCreatePO}
      />

      <UploadReceiptDialog
        open={uploadReceiptOpen}
        purchaseOrder={selectedPO}
        onClose={() => {
          setUploadReceiptOpen(false);
          setSelectedPO(null);
        }}
        onUploadSuccess={handleReceiptUploadSuccess}
        onUpload={apiService.uploadReceiptForPO}
      />

      <UploadInvoiceDialog
        open={uploadInvoiceOpen}
        purchaseOrder={selectedPO}
        onClose={() => {
          setUploadInvoiceOpen(false);
          setSelectedPO(null);
        }}
        onUploadSuccess={handleInvoiceUploadSuccess}
        onUpload={apiService.uploadInvoiceForPO}
      />

      {/* Notifications */}
      <Snackbar
        open={notification.open}
        autoHideDuration={6000}
        onClose={() => setNotification({ ...notification, open: false })}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert
          onClose={() => setNotification({ ...notification, open: false })}
          severity={notification.severity}
          sx={{ width: '100%' }}
        >
          {notification.message}
        </Alert>
      </Snackbar>
    </Box>
  );
};
