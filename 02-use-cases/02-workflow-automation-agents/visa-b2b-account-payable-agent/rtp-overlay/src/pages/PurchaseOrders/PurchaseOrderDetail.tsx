/**
 * Purchase Order Detail Page
 * Displays detailed information about a specific purchase order
 */

import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
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
    email: string;
    phone: string;
    address: string;
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

export const PurchaseOrderDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [po, setPo] = useState<PurchaseOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchPurchaseOrder();
  }, [id]);

  const fetchPurchaseOrder = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/purchase-orders/${id}`);
      if (!response.ok) {
        if (response.status === 404) {
          setError('Purchase order not found');
          return;
        }
        throw new Error('Failed to fetch purchase order');
      }
      const data = await response.json();
      setPo(data);
    } catch (err) {
      console.error('Error fetching purchase order:', err);
      setError(err instanceof Error ? err.message : 'Failed to load purchase order');
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
      month: 'long',
      day: 'numeric',
    });
  };

  if (loading) {
    return (
      <div style={{ padding: oilGasTheme.spacing.xl, textAlign: 'center' }}>
        <p>Loading...</p>
      </div>
    );
  }

  if (error || !po) {
    return (
      <div style={{ padding: oilGasTheme.spacing.xl }}>
        <button
          onClick={() => navigate('/purchase-orders')}
          style={{
            padding: `${oilGasTheme.spacing.sm} ${oilGasTheme.spacing.md}`,
            backgroundColor: 'transparent',
            color: oilGasTheme.colors.primary,
            border: 'none',
            cursor: 'pointer',
            fontSize: oilGasTheme.typography.fontSize.base,
            marginBottom: oilGasTheme.spacing.md,
          }}
        >
          ← Back to Purchase Orders
        </button>
        <div
          style={{
            padding: oilGasTheme.spacing.xl,
            backgroundColor: '#fee',
            border: '1px solid #fcc',
            borderRadius: oilGasTheme.borderRadius.md,
            color: '#c00',
          }}
        >
          {error || 'Purchase order not found'}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div style={{ marginBottom: oilGasTheme.spacing.xl }}>
        <button
          onClick={() => navigate('/purchase-orders')}
          style={{
            padding: `${oilGasTheme.spacing.sm} ${oilGasTheme.spacing.md}`,
            backgroundColor: 'transparent',
            color: oilGasTheme.colors.primary,
            border: 'none',
            cursor: 'pointer',
            fontSize: oilGasTheme.typography.fontSize.base,
            marginBottom: oilGasTheme.spacing.md,
          }}
        >
          ← Back to Purchase Orders
        </button>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
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
              {po.poNumber}
            </h1>
            <p style={{ color: oilGasTheme.colors.textMuted, margin: 0 }}>
              Purchase Order Details
            </p>
          </div>
          <POStatusBadge status={po.status as POStatus} size="lg" />
        </div>
      </div>

      {/* Order Information */}
      <div
        style={{
          backgroundColor: '#ffffff',
          padding: oilGasTheme.spacing.xl,
          borderRadius: oilGasTheme.borderRadius.lg,
          boxShadow: oilGasTheme.shadows.md,
          marginBottom: oilGasTheme.spacing.xl,
        }}
      >
        <h2
          style={{
            fontSize: oilGasTheme.typography.fontSize.xl,
            fontWeight: oilGasTheme.typography.fontWeight.semibold,
            marginBottom: oilGasTheme.spacing.lg,
          }}
        >
          Order Information
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: oilGasTheme.spacing.lg }}>
          <div>
            <p style={{ color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.xs }}>
              Vendor
            </p>
            <p style={{ fontWeight: oilGasTheme.typography.fontWeight.semibold, margin: 0 }}>
              {po.vendor.name}
            </p>
          </div>
          <div>
            <p style={{ color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.xs }}>
              Vendor Code
            </p>
            <p style={{ fontWeight: oilGasTheme.typography.fontWeight.semibold, margin: 0 }}>
              {po.vendor.code}
            </p>
          </div>
          <div>
            <p style={{ color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.xs }}>
              Assigned Date
            </p>
            <p style={{ fontWeight: oilGasTheme.typography.fontWeight.semibold, margin: 0 }}>
              {formatDate(po.assignedDate)}
            </p>
          </div>
          <div>
            <p style={{ color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.xs }}>
              Due Date
            </p>
            <p style={{ fontWeight: oilGasTheme.typography.fontWeight.semibold, margin: 0 }}>
              {formatDate(po.dueDate)}
            </p>
          </div>
          <div>
            <p style={{ color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.xs }}>
              Source Location
            </p>
            <p style={{ fontWeight: oilGasTheme.typography.fontWeight.semibold, margin: 0 }}>
              {po.sourceLocation}
            </p>
          </div>
          <div>
            <p style={{ color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.xs }}>
              Warehouse
            </p>
            <p style={{ fontWeight: oilGasTheme.typography.fontWeight.semibold, margin: 0 }}>
              {po.warehouse}
            </p>
          </div>
        </div>
      </div>

      {/* Line Items */}
      <div
        style={{
          backgroundColor: '#ffffff',
          padding: oilGasTheme.spacing.xl,
          borderRadius: oilGasTheme.borderRadius.lg,
          boxShadow: oilGasTheme.shadows.md,
          marginBottom: oilGasTheme.spacing.xl,
        }}
      >
        <h2
          style={{
            fontSize: oilGasTheme.typography.fontSize.xl,
            fontWeight: oilGasTheme.typography.fontWeight.semibold,
            marginBottom: oilGasTheme.spacing.lg,
          }}
        >
          Line Items
        </h2>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: `2px solid ${oilGasTheme.colors.border}` }}>
              <th style={{ textAlign: 'left', padding: oilGasTheme.spacing.md }}>Description</th>
              <th style={{ textAlign: 'right', padding: oilGasTheme.spacing.md }}>Quantity</th>
              <th style={{ textAlign: 'right', padding: oilGasTheme.spacing.md }}>Received</th>
              <th style={{ textAlign: 'right', padding: oilGasTheme.spacing.md }}>Unit Price</th>
              <th style={{ textAlign: 'right', padding: oilGasTheme.spacing.md }}>Total</th>
            </tr>
          </thead>
          <tbody>
            {po.items.map((item) => (
              <tr key={item.id} style={{ borderBottom: `1px solid ${oilGasTheme.colors.border}` }}>
                <td style={{ padding: oilGasTheme.spacing.md }}>{item.description}</td>
                <td style={{ textAlign: 'right', padding: oilGasTheme.spacing.md }}>
                  {item.quantity.toLocaleString()}
                </td>
                <td style={{ textAlign: 'right', padding: oilGasTheme.spacing.md }}>
                  {item.receivedQuantity.toLocaleString()}
                </td>
                <td style={{ textAlign: 'right', padding: oilGasTheme.spacing.md }}>
                  {formatCurrency(item.unitPrice)}
                </td>
                <td style={{ textAlign: 'right', padding: oilGasTheme.spacing.md }}>
                  {formatCurrency(item.totalPrice)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ borderTop: `2px solid ${oilGasTheme.colors.border}` }}>
              <td colSpan={4} style={{ textAlign: 'right', padding: oilGasTheme.spacing.md, fontWeight: oilGasTheme.typography.fontWeight.bold }}>
                Total Amount:
              </td>
              <td style={{ textAlign: 'right', padding: oilGasTheme.spacing.md, fontWeight: oilGasTheme.typography.fontWeight.bold }}>
                {formatCurrency(po.totalAmount)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      {/* Vendor Contact */}
      <div
        style={{
          backgroundColor: '#ffffff',
          padding: oilGasTheme.spacing.xl,
          borderRadius: oilGasTheme.borderRadius.lg,
          boxShadow: oilGasTheme.shadows.md,
        }}
      >
        <h2
          style={{
            fontSize: oilGasTheme.typography.fontSize.xl,
            fontWeight: oilGasTheme.typography.fontWeight.semibold,
            marginBottom: oilGasTheme.spacing.lg,
          }}
        >
          Vendor Contact
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: oilGasTheme.spacing.lg }}>
          <div>
            <p style={{ color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.xs }}>
              Email
            </p>
            <p style={{ fontWeight: oilGasTheme.typography.fontWeight.semibold, margin: 0 }}>
              {po.vendor.email}
            </p>
          </div>
          <div>
            <p style={{ color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.xs }}>
              Phone
            </p>
            <p style={{ fontWeight: oilGasTheme.typography.fontWeight.semibold, margin: 0 }}>
              {po.vendor.phone}
            </p>
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <p style={{ color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.xs }}>
              Address
            </p>
            <p style={{ fontWeight: oilGasTheme.typography.fontWeight.semibold, margin: 0 }}>
              {po.vendor.address}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
