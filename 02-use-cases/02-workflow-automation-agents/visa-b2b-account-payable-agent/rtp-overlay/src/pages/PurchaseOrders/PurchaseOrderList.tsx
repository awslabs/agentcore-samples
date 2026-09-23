/**
 * Purchase Order List Page
 * Displays all purchase orders with oil & gas styling
 */

import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import POStatusBadge from '../../components/common/POStatusBadge';
import type { POStatus } from '../../components/common/POStatusBadge';
import { oilGasTheme } from '../../styles/oilGasTheme';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'https://56hm3anw06.execute-api.us-east-1.amazonaws.com/prod';

interface PurchaseOrder {
  id: string;
  poNumber: string;
  vendor: {
    id: string;
    name: string;
    code: string;
  };
  sourceLocation: string;
  assignedDate: string;
  dueDate: string;
  status: string;
  totalAmount: number;
  currency: string;
  warehouse: string;
  orderedQuantity: number;
  receivedQuantity: number;
  items: Array<{
    id: string;
    description: string;
    quantity: number;
    receivedQuantity: number;
    unitPrice: number;
    totalPrice: number;
  }>;
}

const PurchaseOrderList: React.FC = () => {
  const navigate = useNavigate();
  const [purchaseOrders, setPurchaseOrders] = useState<PurchaseOrder[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchPurchaseOrders();
  }, []);

  const fetchPurchaseOrders = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/purchase-orders`);

      if (!response.ok) {
        throw new Error('Failed to fetch purchase orders');
      }

      const data = await response.json();
      setPurchaseOrders(data);
    } catch (error) {
      console.error('Error fetching purchase orders:', error);
    } finally {
      setLoading(false);
    }
  };

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
    }).format(amount);
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  };

  return (
    <div>
      {/* Page Header */}
      <div
        style={{
          marginBottom: oilGasTheme.spacing.xl,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <div>
          <h1
            style={{
              fontSize: oilGasTheme.typography.fontSize['3xl'],
              fontWeight: oilGasTheme.typography.fontWeight.bold,
              color: oilGasTheme.colors.text,
              margin: 0,
              marginBottom: oilGasTheme.spacing.xs,
            }}
          >
            Purchase Orders
          </h1>
          <p
            style={{
              fontSize: oilGasTheme.typography.fontSize.base,
              color: oilGasTheme.colors.textMuted,
              margin: 0,
            }}
          >
            Manage procurement orders and specifications
          </p>
        </div>
        <button
          onClick={() => navigate('/purchase-orders/create')}
          style={{
            padding: `${oilGasTheme.spacing.md} ${oilGasTheme.spacing.lg}`,
            backgroundColor: oilGasTheme.colors.primary,
            color: '#ffffff',
            border: 'none',
            borderRadius: oilGasTheme.borderRadius.md,
            fontSize: oilGasTheme.typography.fontSize.base,
            fontWeight: oilGasTheme.typography.fontWeight.semibold,
            cursor: 'pointer',
            transition: 'all 0.2s ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = oilGasTheme.colors.primaryDark;
            e.currentTarget.style.transform = 'translateY(-1px)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = oilGasTheme.colors.primary;
            e.currentTarget.style.transform = 'translateY(0)';
          }}
        >
          + Create PO
        </button>
      </div>

      {/* Summary Cards */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: oilGasTheme.spacing.lg,
          marginBottom: oilGasTheme.spacing.xl,
        }}
      >
        <div
          style={{
            backgroundColor: oilGasTheme.colors.surface,
            padding: oilGasTheme.spacing.lg,
            borderRadius: oilGasTheme.borderRadius.lg,
            boxShadow: oilGasTheme.shadows.sm,
          }}
        >
          <div
            style={{
              fontSize: oilGasTheme.typography.fontSize.sm,
              color: oilGasTheme.colors.textMuted,
              marginBottom: oilGasTheme.spacing.sm,
            }}
          >
            Total POs
          </div>
          <div
            style={{
              fontSize: oilGasTheme.typography.fontSize['3xl'],
              fontWeight: oilGasTheme.typography.fontWeight.bold,
              color: oilGasTheme.colors.primary,
            }}
          >
            {purchaseOrders.length}
          </div>
        </div>
        <div
          style={{
            backgroundColor: oilGasTheme.colors.surface,
            padding: oilGasTheme.spacing.lg,
            borderRadius: oilGasTheme.borderRadius.lg,
            boxShadow: oilGasTheme.shadows.sm,
          }}
        >
          <div
            style={{
              fontSize: oilGasTheme.typography.fontSize.sm,
              color: oilGasTheme.colors.textMuted,
              marginBottom: oilGasTheme.spacing.sm,
            }}
          >
            Total Value
          </div>
          <div
            style={{
              fontSize: oilGasTheme.typography.fontSize['2xl'],
              fontWeight: oilGasTheme.typography.fontWeight.bold,
              color: oilGasTheme.colors.success,
            }}
          >
            {formatCurrency(purchaseOrders.reduce((sum, po) => sum + po.totalAmount, 0))}
          </div>
        </div>
      </div>

      {/* Purchase Orders Table */}
      <div
        style={{
          backgroundColor: oilGasTheme.colors.surface,
          borderRadius: oilGasTheme.borderRadius.lg,
          boxShadow: oilGasTheme.shadows.sm,
          overflow: 'hidden',
        }}
      >
        <table
          style={{
            width: '100%',
            borderCollapse: 'collapse',
          }}
        >
          <thead>
            <tr
              style={{
                backgroundColor: oilGasTheme.colors.background,
                borderBottom: `2px solid ${oilGasTheme.colors.border}`,
              }}
            >
              <th style={tableHeaderStyle}>PO Number</th>
              <th style={tableHeaderStyle}>Vendor</th>
              <th style={tableHeaderStyle}>Material</th>
              <th style={tableHeaderStyle}>Quantity</th>
              <th style={tableHeaderStyle}>Amount</th>
              <th style={tableHeaderStyle}>Delivery Date</th>
              <th style={tableHeaderStyle}>Status</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} style={{ ...tableCellStyle, textAlign: 'center', padding: '2rem' }}>
                  Loading...
                </td>
              </tr>
            ) : purchaseOrders.length === 0 ? (
              <tr>
                <td colSpan={7} style={{ ...tableCellStyle, textAlign: 'center', padding: '2rem' }}>
                  No purchase orders found
                </td>
              </tr>
            ) : (
              purchaseOrders.map((po, index) => (
                <tr
                  key={po.id}
                  onClick={() => navigate(`/purchase-orders/${po.id}`)}
                  style={{
                    borderBottom: `1px solid ${oilGasTheme.colors.border}`,
                    backgroundColor:
                      index % 2 === 0 ? oilGasTheme.colors.surface : oilGasTheme.colors.background,
                    cursor: 'pointer',
                    transition: oilGasTheme.transitions.fast,
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.backgroundColor = oilGasTheme.colors.surfaceHover;
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.backgroundColor =
                      index % 2 === 0 ? oilGasTheme.colors.surface : oilGasTheme.colors.background;
                  }}
                >
                  <td style={tableCellStyle}>
                    <span
                      style={{
                        fontWeight: oilGasTheme.typography.fontWeight.semibold,
                        color: oilGasTheme.colors.primary,
                      }}
                    >
                      {po.poNumber}
                    </span>
                  </td>
                  <td style={tableCellStyle}>{po.vendor.name}</td>
                  <td style={tableCellStyle}>{po.items[0]?.description || 'N/A'}</td>
                  <td style={tableCellStyle}>
                    {po.orderedQuantity.toLocaleString()}
                  </td>
                  <td style={tableCellStyle}>{formatCurrency(po.totalAmount)}</td>
                  <td style={tableCellStyle}>{formatDate(po.dueDate)}</td>
                  <td style={tableCellStyle}>
                    <POStatusBadge status={po.status as POStatus} size="sm" />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const tableHeaderStyle: React.CSSProperties = {
  padding: oilGasTheme.spacing.md,
  textAlign: 'left',
  fontSize: oilGasTheme.typography.fontSize.sm,
  fontWeight: oilGasTheme.typography.fontWeight.semibold,
  color: oilGasTheme.colors.text,
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
};

const tableCellStyle: React.CSSProperties = {
  padding: oilGasTheme.spacing.md,
  fontSize: oilGasTheme.typography.fontSize.sm,
  color: oilGasTheme.colors.text,
};

export default PurchaseOrderList;
