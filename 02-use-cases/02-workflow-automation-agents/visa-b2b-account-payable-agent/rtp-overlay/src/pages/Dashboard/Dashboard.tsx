/**
 * Main Dashboard Page with Charts and Analytics
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../contexts/MockAuthContext';
import { mockDataService } from '../../services/mockDataService';
import { oilGasTheme } from '../../styles/oilGasTheme';
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';

const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [pos] = useState(mockDataService.getPurchaseOrders());
  const [grs] = useState(mockDataService.getGoodsReceipts());
  const [qcs] = useState(mockDataService.getQCRecords());
  const [invoices] = useState(mockDataService.getInvoices());

  const matchRate = invoices.length > 0 ? Math.round((invoices.filter(inv => inv.matchStatus === 'MATCHED').length / invoices.length) * 100) : 0;
  const qcPassRate = qcs.length > 0 ? Math.round((qcs.filter(qc => qc.overallStatus === 'PASS').length / qcs.length) * 100) : 0;

  // VISA Brand Colors
  const VISA_COLORS = {
    navy: '#1A1F71',      // Primary VISA Navy
    gold: '#FFC700',      // VISA Gold
    lightGray: '#E5E7EB', // Light Gray
    mediumGray: '#94A3B8', // Medium Gray
    darkGray: '#475569',  // Dark Gray
  };

  // Chart Data - Stubbed for now, ready for backend integration
  const invoiceProcessingData = [
    { month: 'Jan', processed: 45, pending: 12, rejected: 3 },
    { month: 'Feb', processed: 52, pending: 8, rejected: 2 },
    { month: 'Mar', processed: 61, pending: 15, rejected: 4 },
    { month: 'Apr', processed: 58, pending: 10, rejected: 3 },
    { month: 'May', processed: 67, pending: 9, rejected: 2 },
    { month: 'Jun', processed: 73, pending: 11, rejected: 5 },
  ];

  const paymentMethodData = [
    { name: 'Visa B2B', value: 45, color: VISA_COLORS.navy },
    { name: 'ISO20022', value: 35, color: VISA_COLORS.gold },
    { name: 'Pending', value: 20, color: VISA_COLORS.mediumGray },
  ];

  const matchStatusData = [
    { name: 'Matched', value: invoices.filter(inv => inv.matchStatus === 'MATCHED').length, color: VISA_COLORS.navy },
    { name: 'Mismatched', value: invoices.filter(inv => inv.matchStatus === 'MISMATCHED').length, color: VISA_COLORS.gold },
    { name: 'Pending', value: invoices.filter(inv => inv.matchStatus === 'PENDING').length, color: VISA_COLORS.mediumGray },
  ];

  const weeklyTrendData = [
    { day: 'Mon', invoices: 12, payments: 8 },
    { day: 'Tue', invoices: 15, payments: 10 },
    { day: 'Wed', invoices: 18, payments: 14 },
    { day: 'Thu', invoices: 14, payments: 12 },
    { day: 'Fri', invoices: 20, payments: 16 },
    { day: 'Sat', invoices: 8, payments: 6 },
    { day: 'Sun', invoices: 5, payments: 3 },
  ];

  return (
    <div>
      <div style={{ marginBottom: oilGasTheme.spacing.xl }}>
        <h1 style={{ fontSize: oilGasTheme.typography.fontSize['3xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.text, margin: 0, marginBottom: oilGasTheme.spacing.xs }}>
          Welcome, {user?.name}
        </h1>
        <p style={{ fontSize: oilGasTheme.typography.fontSize.base, color: oilGasTheme.colors.textMuted, margin: 0 }}>
          Oil & Gas Procure-to-Pay Dashboard
        </p>
      </div>

      {/* Summary Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: oilGasTheme.spacing.lg, marginBottom: oilGasTheme.spacing.xl }}>
        <div onClick={() => navigate('/purchase-orders')} style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.xl, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.md, cursor: 'pointer', transition: oilGasTheme.transitions.fast, borderLeft: `4px solid ${oilGasTheme.colors.primary}` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: oilGasTheme.spacing.md }}>
            <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, textTransform: 'uppercase', fontWeight: oilGasTheme.typography.fontWeight.semibold }}>Purchase Orders</div>
            <div style={{ fontSize: oilGasTheme.typography.fontSize['2xl'] }}>📋</div>
          </div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize['4xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.primary, marginBottom: oilGasTheme.spacing.sm }}>{pos.length}</div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted }}>{pos.filter(po => po.status === 'OPEN').length} open orders</div>
        </div>

        <div onClick={() => navigate('/goods-receipts')} style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.xl, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.md, cursor: 'pointer', transition: oilGasTheme.transitions.fast, borderLeft: `4px solid ${oilGasTheme.colors.info}` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: oilGasTheme.spacing.md }}>
            <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, textTransform: 'uppercase', fontWeight: oilGasTheme.typography.fontWeight.semibold }}>Goods Receipts</div>
            <div style={{ fontSize: oilGasTheme.typography.fontSize['2xl'] }}>📦</div>
          </div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize['4xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.info, marginBottom: oilGasTheme.spacing.sm }}>{grs.length}</div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted }}>{grs.filter(gr => gr.matchStatus === 'PENDING').length} pending validation</div>
        </div>

        <div onClick={() => navigate('/quality-control')} style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.xl, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.md, cursor: 'pointer', transition: oilGasTheme.transitions.fast, borderLeft: `4px solid ${oilGasTheme.colors.success}` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: oilGasTheme.spacing.md }}>
            <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, textTransform: 'uppercase', fontWeight: oilGasTheme.typography.fontWeight.semibold }}>QC Pass Rate</div>
            <div style={{ fontSize: oilGasTheme.typography.fontSize['2xl'] }}>🔬</div>
          </div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize['4xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.success, marginBottom: oilGasTheme.spacing.sm }}>{qcPassRate}%</div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted }}>{qcs.length} total tests</div>
        </div>

        <div onClick={() => navigate('/invoices')} style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.xl, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.md, cursor: 'pointer', transition: oilGasTheme.transitions.fast, borderLeft: `4px solid ${oilGasTheme.colors.warning}` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: oilGasTheme.spacing.md }}>
            <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, textTransform: 'uppercase', fontWeight: oilGasTheme.typography.fontWeight.semibold }}>Invoice Match Rate</div>
            <div style={{ fontSize: oilGasTheme.typography.fontSize['2xl'] }}>💰</div>
          </div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize['4xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.warning, marginBottom: oilGasTheme.spacing.sm }}>{matchRate}%</div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted }}>{invoices.filter(inv => inv.matchStatus === 'MISMATCHED').length} mismatched</div>
        </div>
      </div>

      {/* Charts and Analytics */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(500px, 1fr))', gap: oilGasTheme.spacing.lg, marginBottom: oilGasTheme.spacing.xl }}>
        
        {/* Invoice Processing Trend */}
        <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.xl, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm }}>
          <h3 style={{ fontSize: oilGasTheme.typography.fontSize.lg, fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.text, marginBottom: oilGasTheme.spacing.lg }}>
            Invoice Processing Trend
          </h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={invoiceProcessingData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="month" stroke={oilGasTheme.colors.textMuted} />
              <YAxis stroke={oilGasTheme.colors.textMuted} />
              <Tooltip 
                contentStyle={{ 
                  backgroundColor: oilGasTheme.colors.surface, 
                  border: `1px solid ${oilGasTheme.colors.border}`,
                  borderRadius: oilGasTheme.borderRadius.md 
                }} 
              />
              <Legend />
              <Bar dataKey="processed" fill={VISA_COLORS.navy} name="Processed" />
              <Bar dataKey="pending" fill={VISA_COLORS.gold} name="Pending" />
              <Bar dataKey="rejected" fill={VISA_COLORS.mediumGray} name="Rejected" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Payment Method Distribution */}
        <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.xl, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm }}>
          <h3 style={{ fontSize: oilGasTheme.typography.fontSize.lg, fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.text, marginBottom: oilGasTheme.spacing.lg }}>
            Payment Method Distribution
          </h3>
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={paymentMethodData}
                cx="50%"
                cy="50%"
                labelLine={false}
                label={({ name, percent }) => `${name}: ${((percent || 0) * 100).toFixed(0)}%`}
                outerRadius={100}
                fill="#8884d8"
                dataKey="value"
              >
                {paymentMethodData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
          <div style={{ display: 'flex', justifyContent: 'center', gap: oilGasTheme.spacing.lg, marginTop: oilGasTheme.spacing.md }}>
            {paymentMethodData.map((item) => (
              <div key={item.name} style={{ display: 'flex', alignItems: 'center', gap: oilGasTheme.spacing.sm }}>
                <div style={{ width: '12px', height: '12px', backgroundColor: item.color, borderRadius: '2px' }}></div>
                <span style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted }}>
                  {item.name}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Invoice Match Status */}
        <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.xl, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm }}>
          <h3 style={{ fontSize: oilGasTheme.typography.fontSize.lg, fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.text, marginBottom: oilGasTheme.spacing.lg }}>
            Invoice Match Status
          </h3>
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie
                data={matchStatusData}
                cx="50%"
                cy="50%"
                labelLine={false}
                label={({ name, percent }) => `${name}: ${((percent || 0) * 100).toFixed(0)}%`}
                outerRadius={100}
                fill="#8884d8"
                dataKey="value"
              >
                {matchStatusData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
          <div style={{ display: 'flex', justifyContent: 'center', gap: oilGasTheme.spacing.lg, marginTop: oilGasTheme.spacing.md }}>
            {matchStatusData.map((item) => (
              <div key={item.name} style={{ display: 'flex', alignItems: 'center', gap: oilGasTheme.spacing.sm }}>
                <div style={{ width: '12px', height: '12px', backgroundColor: item.color, borderRadius: '2px' }}></div>
                <span style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted }}>
                  {item.name}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Weekly Activity Trend */}
        <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.xl, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm }}>
          <h3 style={{ fontSize: oilGasTheme.typography.fontSize.lg, fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.text, marginBottom: oilGasTheme.spacing.lg }}>
            Weekly Activity Trend
          </h3>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={weeklyTrendData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="day" stroke={oilGasTheme.colors.textMuted} />
              <YAxis stroke={oilGasTheme.colors.textMuted} />
              <Tooltip 
                contentStyle={{ 
                  backgroundColor: oilGasTheme.colors.surface, 
                  border: `1px solid ${oilGasTheme.colors.border}`,
                  borderRadius: oilGasTheme.borderRadius.md 
                }} 
              />
              <Legend />
              <Line type="monotone" dataKey="invoices" stroke={VISA_COLORS.navy} strokeWidth={2} name="Invoices" />
              <Line type="monotone" dataKey="payments" stroke={VISA_COLORS.gold} strokeWidth={2} name="Payments" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Recent Activity */}
      <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.xl, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm, marginBottom: oilGasTheme.spacing.xl }}>
        <h2 style={{ fontSize: oilGasTheme.typography.fontSize.xl, fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.text, marginBottom: oilGasTheme.spacing.lg }}>Recent Activity</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: oilGasTheme.spacing.md }}>
          {invoices.filter(inv => inv.matchStatus === 'MISMATCHED').map(inv => (
            <div key={inv.id} style={{ padding: oilGasTheme.spacing.md, backgroundColor: oilGasTheme.colors.danger + '10', borderRadius: oilGasTheme.borderRadius.md, borderLeft: `3px solid ${oilGasTheme.colors.danger}` }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontSize: oilGasTheme.typography.fontSize.base, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text }}>
                    Invoice {inv.invoiceNumber} - Mismatch Detected
                  </div>
                  <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, marginTop: '4px' }}>
                    {inv.vendorName} • {inv.validationFlags.join(', ')}
                  </div>
                </div>
                <button onClick={() => navigate(`/invoices/${inv.id}`)} style={{ padding: `${oilGasTheme.spacing.sm} ${oilGasTheme.spacing.md}`, backgroundColor: oilGasTheme.colors.danger, color: '#ffffff', border: 'none', borderRadius: oilGasTheme.borderRadius.md, fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, cursor: 'pointer' }}>
                  Review
                </button>
              </div>
            </div>
          ))}
          {qcs.filter(qc => qc.overallStatus === 'FAIL').map(qc => (
            <div key={qc.id} style={{ padding: oilGasTheme.spacing.md, backgroundColor: oilGasTheme.colors.warning + '10', borderRadius: oilGasTheme.borderRadius.md, borderLeft: `3px solid ${oilGasTheme.colors.warning}` }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontSize: oilGasTheme.typography.fontSize.base, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text }}>
                    QC Test {qc.qcNumber} - Failed
                  </div>
                  <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, marginTop: '4px' }}>
                    {qc.materialDescription} • {qc.inspectorName}
                  </div>
                </div>
                <button onClick={() => navigate(`/quality-control/${qc.id}`)} style={{ padding: `${oilGasTheme.spacing.sm} ${oilGasTheme.spacing.md}`, backgroundColor: oilGasTheme.colors.warning, color: '#ffffff', border: 'none', borderRadius: oilGasTheme.borderRadius.md, fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, cursor: 'pointer' }}>
                  View
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
