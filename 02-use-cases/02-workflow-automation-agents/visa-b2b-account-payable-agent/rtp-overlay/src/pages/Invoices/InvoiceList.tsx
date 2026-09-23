/**
 * Invoice List Page with Match Status
 */

import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import MatchStatusBadge from '../../components/common/MatchStatusBadge';
import { oilGasTheme } from '../../styles/oilGasTheme';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'https://56hm3anw06.execute-api.us-east-1.amazonaws.com/prod';

interface Invoice {
  id: string;
  invoiceNumber: string;
  vendorName: string;
  totalAmount: number;
  invoiceDate: string;
  matchStatus: 'MATCHED' | 'MISMATCHED' | 'PENDING';
  validationFlags: string[];
}

const InvoiceList: React.FC = () => {
  const navigate = useNavigate();
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchInvoices();
  }, []);

  const fetchInvoices = async () => {
    try {
      setLoading(true);
      const token = localStorage.getItem('accessToken');
      const headers: HeadersInit = {
        'Content-Type': 'application/json',
      };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const response = await fetch(`${API_BASE_URL}/api/invoices?limit=100`, {
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch invoices: ${response.statusText}`);
      }

      const result = await response.json();
      const invoiceData = result.data || result;

      // Transform backend data to component format
      const transformedInvoices = invoiceData.map((inv: any) => ({
        id: inv.id,
        invoiceNumber: inv.invoiceNumber,
        vendorName: inv.vendor?.name || 'Unknown Vendor',
        totalAmount: parseFloat(inv.totalAmount),
        invoiceDate: inv.invoiceDate,
        matchStatus: inv.matchStatus || 'PENDING',
        validationFlags: inv.validationFlags || [],
      }));

      setInvoices(transformedInvoices);
      setError(null);
    } catch (err) {
      console.error('Error fetching invoices:', err);
      setError(err instanceof Error ? err.message : 'Failed to load invoices');
      setInvoices([]);
    } finally {
      setLoading(false);
    }
  };

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount);
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
  };

  const matchedCount = invoices.filter(inv => inv.matchStatus === 'MATCHED').length;
  const mismatchedCount = invoices.filter(inv => inv.matchStatus === 'MISMATCHED').length;
  const pendingCount = invoices.filter(inv => inv.matchStatus === 'PENDING').length;

  if (loading) {
    return (
      <div style={{ padding: oilGasTheme.spacing.xl, textAlign: 'center' }}>
        <p style={{ fontSize: oilGasTheme.typography.fontSize.lg, color: oilGasTheme.colors.textMuted }}>
          Loading invoices...
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
          onClick={fetchInvoices}
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
            Invoice Dashboard
          </h1>
          <p style={{ fontSize: oilGasTheme.typography.fontSize.base, color: oilGasTheme.colors.textMuted, margin: 0 }}>
            {invoices.length === 0 ? 'No invoices yet. Upload your first invoice to get started.' : '4-way match validation and payment approval'}
          </p>
        </div>
        <button
          onClick={() => navigate('/invoices/upload')}
          style={{ padding: `${oilGasTheme.spacing.md} ${oilGasTheme.spacing.lg}`, backgroundColor: oilGasTheme.colors.primary, color: '#ffffff', border: 'none', borderRadius: oilGasTheme.borderRadius.md, fontSize: oilGasTheme.typography.fontSize.base, fontWeight: oilGasTheme.typography.fontWeight.semibold, cursor: 'pointer' }}
        >
          📄 Upload Invoice
        </button>
      </div>

      {/* Summary Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: oilGasTheme.spacing.lg, marginBottom: oilGasTheme.spacing.xl }}>
        <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.lg, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm }}>
          <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.sm }}>Total Invoices</div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize['3xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.primary }}>{invoices.length}</div>
        </div>
        <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.lg, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm }}>
          <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.sm }}>Matched</div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize['3xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.success }}>{matchedCount}</div>
        </div>
        <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.lg, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm }}>
          <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.sm }}>Mismatched</div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize['3xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.danger }}>{mismatchedCount}</div>
        </div>
        <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.lg, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm }}>
          <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.sm }}>Pending</div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize['3xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.warning }}>{pendingCount}</div>
        </div>
      </div>

      {invoices.length === 0 ? (
        <div style={{ backgroundColor: oilGasTheme.colors.surface, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm, padding: oilGasTheme.spacing.xl, textAlign: 'center' }}>
          <p style={{ fontSize: oilGasTheme.typography.fontSize.lg, color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.md }}>
            No invoices found
          </p>
          <p style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted }}>
            Upload your first invoice to get started
          </p>
        </div>
      ) : (
        <div style={{ backgroundColor: oilGasTheme.colors.surface, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ backgroundColor: oilGasTheme.colors.background, borderBottom: `2px solid ${oilGasTheme.colors.border}` }}>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Invoice #</th>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Vendor</th>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Amount</th>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Date</th>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Match Status</th>
                <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Flags</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((invoice, index) => (
              <tr
                key={invoice.id}
                onClick={() => navigate(`/invoices/${invoice.id}`)}
                style={{
                  borderBottom: `1px solid ${oilGasTheme.colors.border}`,
                  backgroundColor: invoice.matchStatus === 'MATCHED' ? oilGasTheme.colors.success + '10' : invoice.matchStatus === 'MISMATCHED' ? oilGasTheme.colors.danger + '10' : index % 2 === 0 ? oilGasTheme.colors.surface : oilGasTheme.colors.background,
                  cursor: 'pointer'
                }}
              >
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.primary, fontWeight: oilGasTheme.typography.fontWeight.semibold }}>{invoice.invoiceNumber}</td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.text }}>{invoice.vendorName}</td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.text }}>{formatCurrency(invoice.totalAmount)}</td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.text }}>{formatDate(invoice.invoiceDate)}</td>
                <td style={{ padding: oilGasTheme.spacing.md }}><MatchStatusBadge status={invoice.matchStatus} size="sm" /></td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.xs, color: oilGasTheme.colors.textMuted }}>
                  {invoice.validationFlags.length > 0 ? invoice.validationFlags.join(', ') : '-'}
                </td>
              </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default InvoiceList;
