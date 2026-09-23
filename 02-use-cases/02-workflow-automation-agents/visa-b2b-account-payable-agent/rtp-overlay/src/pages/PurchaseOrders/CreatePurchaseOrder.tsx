/**
 * Create Purchase Order Page
 * Form to create a new purchase order
 */

import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { oilGasTheme } from '../../styles/oilGasTheme';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'https://56hm3anw06.execute-api.us-east-1.amazonaws.com/prod';

interface Vendor {
  id: string;
  name: string;
  code: string;
}

interface POItem {
  description: string;
  quantity: number;
  unitPrice: number;
}

export const CreatePurchaseOrder: React.FC = () => {
  const navigate = useNavigate();
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [formData, setFormData] = useState({
    vendorId: '',
    sourceLocation: '',
    warehouse: '',
    dueDate: '',
    items: [{ description: '', quantity: 0, unitPrice: 0 }] as POItem[],
  });

  useEffect(() => {
    fetchVendors();
  }, []);

  const fetchVendors = async () => {
    try {
      const token = localStorage.getItem('accessToken');
      const headers: HeadersInit = {
        'Content-Type': 'application/json',
      };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const response = await fetch(`${API_BASE_URL}/api/vendors`, { headers });
      if (!response.ok) throw new Error('Failed to fetch vendors');
      const result = await response.json();
      // Handle paginated response
      setVendors(result.data || result);
    } catch (err) {
      console.error('Error fetching vendors:', err);
      setError('Failed to load vendors');
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const token = localStorage.getItem('accessToken');
      const headers: HeadersInit = {
        'Content-Type': 'application/json',
      };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const response = await fetch(`${API_BASE_URL}/api/purchase-orders`, {
        method: 'POST',
        headers,
        body: JSON.stringify(formData),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.message || 'Failed to create purchase order');
      }

      await response.json();
      navigate(`/purchase-orders`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create purchase order');
    } finally {
      setLoading(false);
    }
  };

  const addItem = () => {
    setFormData({
      ...formData,
      items: [...formData.items, { description: '', quantity: 0, unitPrice: 0 }],
    });
  };

  const removeItem = (index: number) => {
    setFormData({
      ...formData,
      items: formData.items.filter((_, i) => i !== index),
    });
  };

  const updateItem = (index: number, field: keyof POItem, value: string | number) => {
    const newItems = [...formData.items];
    newItems[index] = { ...newItems[index], [field]: value };
    setFormData({ ...formData, items: newItems });
  };

  const calculateTotal = () => {
    return formData.items.reduce((sum, item) => sum + item.quantity * item.unitPrice, 0);
  };

  const inputStyle = {
    width: '100%',
    padding: oilGasTheme.spacing.md,
    border: `1px solid ${oilGasTheme.colors.border}`,
    borderRadius: oilGasTheme.borderRadius.md,
    fontSize: oilGasTheme.typography.fontSize.base,
    fontFamily: oilGasTheme.typography.fontFamily,
  };

  const labelStyle = {
    display: 'block',
    marginBottom: oilGasTheme.spacing.sm,
    fontSize: oilGasTheme.typography.fontSize.sm,
    fontWeight: oilGasTheme.typography.fontWeight.semibold,
    color: oilGasTheme.colors.text,
  };

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
        <h1
          style={{
            fontSize: oilGasTheme.typography.fontSize['3xl'],
            fontWeight: oilGasTheme.typography.fontWeight.bold,
            color: oilGasTheme.colors.text,
            margin: 0,
          }}
        >
          Create Purchase Order
        </h1>
      </div>

      {error && (
        <div
          style={{
            padding: oilGasTheme.spacing.md,
            backgroundColor: '#fee',
            border: '1px solid #fcc',
            borderRadius: oilGasTheme.borderRadius.md,
            marginBottom: oilGasTheme.spacing.lg,
            color: '#c00',
          }}
        >
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit}>
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
            Order Details
          </h2>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: oilGasTheme.spacing.lg }}>
            <div>
              <label style={labelStyle}>Vendor *</label>
              <select
                required
                value={formData.vendorId}
                onChange={(e) => setFormData({ ...formData, vendorId: e.target.value })}
                style={inputStyle}
              >
                <option value="">Select a vendor</option>
                {vendors.map((vendor) => (
                  <option key={vendor.id} value={vendor.id}>
                    {vendor.name} ({vendor.code})
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label style={labelStyle}>Due Date *</label>
              <input
                type="date"
                required
                value={formData.dueDate}
                onChange={(e) => setFormData({ ...formData, dueDate: e.target.value })}
                style={inputStyle}
              />
            </div>

            <div>
              <label style={labelStyle}>Source Location *</label>
              <input
                type="text"
                required
                value={formData.sourceLocation}
                onChange={(e) => setFormData({ ...formData, sourceLocation: e.target.value })}
                style={inputStyle}
                placeholder="e.g., Warehouse A"
              />
            </div>

            <div>
              <label style={labelStyle}>Warehouse *</label>
              <input
                type="text"
                required
                value={formData.warehouse}
                onChange={(e) => setFormData({ ...formData, warehouse: e.target.value })}
                style={inputStyle}
                placeholder="e.g., Warehouse A"
              />
            </div>
          </div>
        </div>

        <div
          style={{
            backgroundColor: '#ffffff',
            padding: oilGasTheme.spacing.xl,
            borderRadius: oilGasTheme.borderRadius.lg,
            boxShadow: oilGasTheme.shadows.md,
            marginBottom: oilGasTheme.spacing.xl,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: oilGasTheme.spacing.lg }}>
            <h2
              style={{
                fontSize: oilGasTheme.typography.fontSize.xl,
                fontWeight: oilGasTheme.typography.fontWeight.semibold,
                margin: 0,
              }}
            >
              Line Items
            </h2>
            <button
              type="button"
              onClick={addItem}
              style={{
                padding: `${oilGasTheme.spacing.sm} ${oilGasTheme.spacing.md}`,
                backgroundColor: oilGasTheme.colors.primary,
                color: '#ffffff',
                border: 'none',
                borderRadius: oilGasTheme.borderRadius.md,
                cursor: 'pointer',
                fontSize: oilGasTheme.typography.fontSize.sm,
              }}
            >
              + Add Item
            </button>
          </div>

          {formData.items.map((item, index) => (
            <div
              key={index}
              style={{
                padding: oilGasTheme.spacing.lg,
                border: `1px solid ${oilGasTheme.colors.border}`,
                borderRadius: oilGasTheme.borderRadius.md,
                marginBottom: oilGasTheme.spacing.md,
              }}
            >
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr auto', gap: oilGasTheme.spacing.md, alignItems: 'end' }}>
                <div>
                  <label style={labelStyle}>Description *</label>
                  <input
                    type="text"
                    required
                    value={item.description}
                    onChange={(e) => updateItem(index, 'description', e.target.value)}
                    style={inputStyle}
                    placeholder="Item description"
                  />
                </div>
                <div>
                  <label style={labelStyle}>Quantity *</label>
                  <input
                    type="number"
                    required
                    min="1"
                    value={item.quantity || ''}
                    onChange={(e) => updateItem(index, 'quantity', parseFloat(e.target.value) || 0)}
                    style={inputStyle}
                  />
                </div>
                <div>
                  <label style={labelStyle}>Unit Price *</label>
                  <input
                    type="number"
                    required
                    min="0"
                    step="0.01"
                    value={item.unitPrice || ''}
                    onChange={(e) => updateItem(index, 'unitPrice', parseFloat(e.target.value) || 0)}
                    style={inputStyle}
                  />
                </div>
                {formData.items.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeItem(index)}
                    style={{
                      padding: oilGasTheme.spacing.md,
                      backgroundColor: '#fee',
                      color: '#c00',
                      border: '1px solid #fcc',
                      borderRadius: oilGasTheme.borderRadius.md,
                      cursor: 'pointer',
                    }}
                  >
                    Remove
                  </button>
                )}
              </div>
              <div style={{ marginTop: oilGasTheme.spacing.sm, textAlign: 'right', color: oilGasTheme.colors.textMuted }}>
                Subtotal: ${(item.quantity * item.unitPrice).toFixed(2)}
              </div>
            </div>
          ))}

          <div
            style={{
              marginTop: oilGasTheme.spacing.lg,
              paddingTop: oilGasTheme.spacing.lg,
              borderTop: `2px solid ${oilGasTheme.colors.border}`,
              textAlign: 'right',
            }}
          >
            <div style={{ fontSize: oilGasTheme.typography.fontSize.xl, fontWeight: oilGasTheme.typography.fontWeight.bold }}>
              Total: ${calculateTotal().toFixed(2)}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: oilGasTheme.spacing.md, justifyContent: 'flex-end' }}>
          <button
            type="button"
            onClick={() => navigate('/purchase-orders')}
            style={{
              padding: `${oilGasTheme.spacing.md} ${oilGasTheme.spacing.xl}`,
              backgroundColor: '#ffffff',
              color: oilGasTheme.colors.text,
              border: `1px solid ${oilGasTheme.colors.border}`,
              borderRadius: oilGasTheme.borderRadius.md,
              cursor: 'pointer',
              fontSize: oilGasTheme.typography.fontSize.base,
            }}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={loading}
            style={{
              padding: `${oilGasTheme.spacing.md} ${oilGasTheme.spacing.xl}`,
              backgroundColor: loading ? oilGasTheme.colors.textMuted : oilGasTheme.colors.primary,
              color: '#ffffff',
              border: 'none',
              borderRadius: oilGasTheme.borderRadius.md,
              cursor: loading ? 'not-allowed' : 'pointer',
              fontSize: oilGasTheme.typography.fontSize.base,
              fontWeight: oilGasTheme.typography.fontWeight.semibold,
            }}
          >
            {loading ? 'Creating...' : 'Create Purchase Order'}
          </button>
        </div>
      </form>
    </div>
  );
};
