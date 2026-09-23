import React, { useEffect, useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  Paper,
  Skeleton,
  IconButton,
} from '@mui/material';
import {
  TrendingUp,
  Receipt,
  Payment,
  Business,
  ArrowForward,
  Refresh,
  Description,
  AccountBalance,
  Warning,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import {
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
import { useInvoice } from '../../contexts/InvoiceContext';
import { paymentService } from '../../services/paymentService';
import { AIInsightsPanel } from './AIInsightsPanel';

interface DashboardStats {
  invoices: {
    total: number;
    pending: number;
    overdue: number;
    totalAmount: number;
  };
  payments: {
    total: number;
    ready: number;
    sent: number;
    totalAmount: number;
  };
  vendors: {
    total: number;
    active: number;
  };
}

export const TreasuryDashboard: React.FC = () => {
  const navigate = useNavigate();
  const { fetchSummary, summary: invoiceSummary } = useInvoice();
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [paymentTrend, setPaymentTrend] = useState<any[]>([]);
  const [statusDistribution, setStatusDistribution] = useState<any[]>([]);

  useEffect(() => {
    loadDashboardData();
  }, []);

  const loadDashboardData = async () => {
    setLoading(true);
    try {
      // Fetch invoice summary
      await fetchSummary();

      // Fetch payment summary
      const paymentSummary = await paymentService.getPaymentSummary();

      // Combine stats
      setStats({
        invoices: {
          total: invoiceSummary?.totalInvoices || 0,
          pending: invoiceSummary?.pendingPayments || 0,
          overdue: invoiceSummary?.overdueInvoices || 0,
          totalAmount: invoiceSummary?.totalAmount || 0,
        },
        payments: {
          total: paymentSummary.totalPendingPayments,
          ready: paymentSummary.paymentsByStatus.ready,
          sent: paymentSummary.paymentsByStatus.sent,
          totalAmount: paymentSummary.totalPaymentAmount,
        },
        vendors: {
          total: 15, // Mock for now
          active: 12,
        },
      });

      // Generate payment trend data (last 7 days)
      const trendData = generateTrendData();
      setPaymentTrend(trendData);

      // Status distribution
      setStatusDistribution([
        { name: 'Ready', value: paymentSummary.paymentsByStatus.ready, color: '#2196F3' },
        { name: 'Scheduled', value: paymentSummary.paymentsByStatus.scheduled, color: '#9C27B0' },
        { name: 'Sent', value: paymentSummary.paymentsByStatus.sent, color: '#4CAF50' },
        { name: 'Paid', value: paymentSummary.paymentsByStatus.paid, color: '#66BB6A' },
      ]);
    } catch (error) {
      console.error('Error loading dashboard data:', error);
    } finally {
      setLoading(false);
    }
  };

  const generateTrendData = () => {
    const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    return days.map((day) => ({
      day,
      payments: Math.floor(Math.random() * 50000) + 10000,
      invoices: Math.floor(Math.random() * 40000) + 8000,
    }));
  };

  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(amount);
  };

  return (
    <Box sx={{ p: 3 }}>
      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Box>
          <Typography variant="h4" fontWeight="bold" gutterBottom>
            Treasury Dashboard
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Overview of invoices, payments, and financial operations
          </Typography>
        </Box>
        <IconButton onClick={loadDashboardData} color="primary">
          <Refresh />
        </IconButton>
      </Box>

      {/* Overview Cards */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', md: 'repeat(4, 1fr)' },
          gap: 3,
          mb: 4,
        }}
      >
        {/* Invoices Card */}
        <Card
          sx={{
            background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
            color: 'white',
            height: '100%',
          }}
        >
          <CardContent>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <Box>
                <Typography variant="body2" sx={{ opacity: 0.9, mb: 1 }}>
                  Total Invoices
                </Typography>
                {loading ? (
                  <Skeleton variant="text" width={80} sx={{ bgcolor: 'rgba(255,255,255,0.2)' }} />
                ) : (
                  <Typography variant="h4" fontWeight="bold">
                    {stats?.invoices.total || 0}
                  </Typography>
                )}
                <Box sx={{ display: 'flex', alignItems: 'center', mt: 1 }}>
                  <TrendingUp sx={{ fontSize: 16, mr: 0.5 }} />
                  <Typography variant="caption">{stats?.invoices.pending || 0} pending</Typography>
                </Box>
              </Box>
              <Receipt sx={{ fontSize: 40, opacity: 0.3 }} />
            </Box>
            <Button
              size="small"
              endIcon={<ArrowForward />}
              onClick={() => navigate('/invoices')}
              sx={{ mt: 2, color: 'white', borderColor: 'rgba(255,255,255,0.3)' }}
              variant="outlined"
            >
              View All
            </Button>
          </CardContent>
        </Card>

        {/* Payments Card */}
        <Card
          sx={{
            background: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)',
            color: 'white',
            height: '100%',
          }}
        >
          <CardContent>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <Box>
                <Typography variant="body2" sx={{ opacity: 0.9, mb: 1 }}>
                  Pending Payments
                </Typography>
                {loading ? (
                  <Skeleton variant="text" width={80} sx={{ bgcolor: 'rgba(255,255,255,0.2)' }} />
                ) : (
                  <Typography variant="h4" fontWeight="bold">
                    {stats?.payments.total || 0}
                  </Typography>
                )}
                <Box sx={{ display: 'flex', alignItems: 'center', mt: 1 }}>
                  <AccountBalance sx={{ fontSize: 16, mr: 0.5 }} />
                  <Typography variant="caption">{formatCurrency(stats?.payments.totalAmount || 0)}</Typography>
                </Box>
              </Box>
              <Payment sx={{ fontSize: 40, opacity: 0.3 }} />
            </Box>
            <Button
              size="small"
              endIcon={<ArrowForward />}
              onClick={() => navigate('/payments')}
              sx={{ mt: 2, color: 'white', borderColor: 'rgba(255,255,255,0.3)' }}
              variant="outlined"
            >
              View All
            </Button>
          </CardContent>
        </Card>

        {/* Overdue Card */}
        <Card
          sx={{
            background: 'linear-gradient(135deg, #fa709a 0%, #fee140 100%)',
            color: 'white',
            height: '100%',
          }}
        >
          <CardContent>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <Box>
                <Typography variant="body2" sx={{ opacity: 0.9, mb: 1 }}>
                  Overdue Invoices
                </Typography>
                {loading ? (
                  <Skeleton variant="text" width={80} sx={{ bgcolor: 'rgba(255,255,255,0.2)' }} />
                ) : (
                  <Typography variant="h4" fontWeight="bold">
                    {stats?.invoices.overdue || 0}
                  </Typography>
                )}
                <Box sx={{ display: 'flex', alignItems: 'center', mt: 1 }}>
                  <Warning sx={{ fontSize: 16, mr: 0.5 }} />
                  <Typography variant="caption">Requires attention</Typography>
                </Box>
              </Box>
              <Warning sx={{ fontSize: 40, opacity: 0.3 }} />
            </Box>
            <Button
              size="small"
              endIcon={<ArrowForward />}
              onClick={() => navigate('/invoices')}
              sx={{ mt: 2, color: 'white', borderColor: 'rgba(255,255,255,0.3)' }}
              variant="outlined"
            >
              View All
            </Button>
          </CardContent>
        </Card>

        {/* Vendors Card */}
        <Card
          sx={{
            background: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)',
            color: 'white',
            height: '100%',
          }}
        >
          <CardContent>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <Box>
                <Typography variant="body2" sx={{ opacity: 0.9, mb: 1 }}>
                  Active Vendors
                </Typography>
                {loading ? (
                  <Skeleton variant="text" width={80} sx={{ bgcolor: 'rgba(255,255,255,0.2)' }} />
                ) : (
                  <Typography variant="h4" fontWeight="bold">
                    {stats?.vendors.active || 0}
                  </Typography>
                )}
                <Box sx={{ display: 'flex', alignItems: 'center', mt: 1 }}>
                  <Business sx={{ fontSize: 16, mr: 0.5 }} />
                  <Typography variant="caption">of {stats?.vendors.total || 0} total</Typography>
                </Box>
              </Box>
              <Business sx={{ fontSize: 40, opacity: 0.3 }} />
            </Box>
            <Button
              size="small"
              endIcon={<ArrowForward />}
              onClick={() => navigate('/delivery/purchase-orders')}
              sx={{ mt: 2, color: 'white', borderColor: 'rgba(255,255,255,0.3)' }}
              variant="outlined"
            >
              View All
            </Button>
          </CardContent>
        </Card>
      </Box>

      {/* Charts Row */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', md: '2fr 1fr' },
          gap: 3,
          mb: 4,
        }}
      >
        {/* Payment Trend Chart */}
        <Paper sx={{ p: 3, height: '100%' }}>
          <Typography variant="h6" fontWeight="bold" gutterBottom>
            Payment & Invoice Trend (Last 7 Days)
          </Typography>
          {loading ? (
            <Skeleton variant="rectangular" height={300} />
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={paymentTrend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="day" stroke="#666" />
                <YAxis stroke="#666" />
                <Tooltip
                  contentStyle={{ borderRadius: 8, border: '1px solid #e0e0e0' }}
                  formatter={(value: number | undefined) => value !== undefined ? formatCurrency(value) : ''}
                />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="payments"
                  stroke="#f5576c"
                  strokeWidth={3}
                  dot={{ fill: '#f5576c', r: 5 }}
                  name="Payments"
                />
                <Line
                  type="monotone"
                  dataKey="invoices"
                  stroke="#667eea"
                  strokeWidth={3}
                  dot={{ fill: '#667eea', r: 5 }}
                  name="Invoices"
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Paper>

        {/* Payment Status Distribution */}
        <Paper sx={{ p: 3, height: '100%' }}>
          <Typography variant="h6" fontWeight="bold" gutterBottom>
            Payment Status
          </Typography>
          {loading ? (
            <Skeleton variant="circular" width={200} height={200} sx={{ mx: 'auto' }} />
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie
                  data={statusDistribution}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={({ name, percent }: any) => `${name} ${(percent * 100).toFixed(0)}%`}
                  outerRadius={80}
                  fill="#8884d8"
                  dataKey="value"
                >
                  {statusDistribution.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          )}
        </Paper>
      </Box>

      {/* AI Insights Panel */}
      <AIInsightsPanel />

      {/* Quick Actions */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', md: 'repeat(3, 1fr)' },
          gap: 3,
          mt: 4,
        }}
      >
        <Card sx={{ height: '100%', cursor: 'pointer', '&:hover': { boxShadow: 4 } }} onClick={() => navigate('/invoices')}>
          <CardContent>
            <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
              <Description sx={{ fontSize: 40, color: '#667eea', mr: 2 }} />
              <Box>
                <Typography variant="h6" fontWeight="bold">
                  Invoice Management
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Review and process invoices
                </Typography>
              </Box>
            </Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              View all invoices, match with POs, and approve for payment
            </Typography>
            <Button endIcon={<ArrowForward />} fullWidth variant="outlined">
              Go to Invoices
            </Button>
          </CardContent>
        </Card>

        <Card sx={{ height: '100%', cursor: 'pointer', '&:hover': { boxShadow: 4 } }} onClick={() => navigate('/payments')}>
          <CardContent>
            <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
              <Payment sx={{ fontSize: 40, color: '#f5576c', mr: 2 }} />
              <Box>
                <Typography variant="h6" fontWeight="bold">
                  Payment Hub
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Process and track payments
                </Typography>
              </Box>
            </Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Download ISO 20022 files and manage payment status
            </Typography>
            <Button endIcon={<ArrowForward />} fullWidth variant="outlined">
              Go to Payments
            </Button>
          </CardContent>
        </Card>

        <Card sx={{ height: '100%', cursor: 'pointer', '&:hover': { boxShadow: 4 } }} onClick={() => navigate('/delivery/purchase-orders')}>
          <CardContent>
            <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
              <Business sx={{ fontSize: 40, color: '#4facfe', mr: 2 }} />
              <Box>
                <Typography variant="h6" fontWeight="bold">
                  Purchase Orders
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  View POs and vendors
                </Typography>
              </Box>
            </Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Track purchase orders and vendor performance
            </Typography>
            <Button endIcon={<ArrowForward />} fullWidth variant="outlined">
              Go to POs
            </Button>
          </CardContent>
        </Card>
      </Box>
    </Box>
  );
};
