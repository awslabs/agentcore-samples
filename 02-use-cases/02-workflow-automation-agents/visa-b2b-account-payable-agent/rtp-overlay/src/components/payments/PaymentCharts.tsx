import React, { useMemo } from 'react';
import { Box, Card, CardContent, Typography, Skeleton } from '@mui/material';
import {
  LineChart,
  Line,
  PieChart,
  Pie,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import type { Payment, PaymentSummary } from '../../types/payment.types';
import { paymentStatusColors } from '../../theme/paymentColors';

interface PaymentChartsProps {
  payments: Payment[];
  summary: PaymentSummary | null;
  loading: boolean;
}

const LoadingChart: React.FC<{ title: string }> = ({ title }) => {
  return (
    <Card>
      <CardContent>
        <Typography variant="h6" gutterBottom>
          {title}
        </Typography>
        <Skeleton variant="rectangular" height={300} />
      </CardContent>
    </Card>
  );
};

export const PaymentCharts: React.FC<PaymentChartsProps> = ({
  payments,
  summary,
  loading,
}) => {
  // Prepare data for payment trend chart (last 30 days)
  const trendData = useMemo(() => {
    const last30Days = Array.from({ length: 30 }, (_, i) => {
      const date = new Date();
      date.setDate(date.getDate() - (29 - i));
      return date.toISOString().split('T')[0];
    });

    return last30Days.map((date) => {
      const dayPayments = payments.filter(
        (p) => p.invoiceDate.split('T')[0] === date
      );
      const total = dayPayments.reduce((sum, p) => sum + p.amount, 0);

      return {
        date: new Date(date).toLocaleDateString('en-US', {
          month: 'short',
          day: 'numeric',
        }),
        amount: total,
      };
    });
  }, [payments]);

  // Prepare data for status distribution pie chart
  const statusData = useMemo(() => {
    if (!summary) return [];

    return [
      { name: 'Ready', value: summary.paymentsByStatus.ready, color: paymentStatusColors.ready.main },
      { name: 'Scheduled', value: summary.paymentsByStatus.scheduled, color: paymentStatusColors.scheduled.main },
      { name: 'Processing', value: summary.paymentsByStatus.processing, color: paymentStatusColors.processing.main },
      { name: 'Sent', value: summary.paymentsByStatus.sent, color: paymentStatusColors.sent.main },
      { name: 'Paid', value: summary.paymentsByStatus.paid, color: paymentStatusColors.paid.main },
      { name: 'Failed', value: summary.paymentsByStatus.failed, color: paymentStatusColors.failed.main },
      { name: 'Cancelled', value: summary.paymentsByStatus.cancelled, color: paymentStatusColors.cancelled.main },
    ].filter((item) => item.value > 0);
  }, [summary]);

  // Prepare data for top 5 vendors bar chart
  const vendorData = useMemo(() => {
    const vendorTotals = payments.reduce((acc, payment) => {
      const vendorName = payment.vendor.name;
      if (!acc[vendorName]) {
        acc[vendorName] = 0;
      }
      acc[vendorName] += payment.amount;
      return acc;
    }, {} as Record<string, number>);

    return Object.entries(vendorTotals)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 5)
      .map(([name, amount]) => ({
        vendor: name.length > 20 ? name.substring(0, 20) + '...' : name,
        amount,
      }));
  }, [payments]);

  if (loading) {
    return (
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
          gap: 3,
          mb: 4,
        }}
      >
        <LoadingChart title="Payment Trend (Last 30 Days)" />
        <LoadingChart title="Payment Status Distribution" />
        <Box sx={{ gridColumn: { xs: '1', md: '1 / -1' } }}>
          <LoadingChart title="Top 5 Vendors by Payment Amount" />
        </Box>
      </Box>
    );
  }

  const formatCurrency = (value: number): string => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(value);
  };

  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
        gap: 3,
        mb: 4,
      }}
    >
      {/* Payment Trend Line Chart */}
      <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom sx={{ fontWeight: 600 }}>
              Payment Trend (Last 30 Days)
            </Typography>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={trendData}>
                <defs>
                  <linearGradient id="colorAmount" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#667eea" stopOpacity={0.8} />
                    <stop offset="95%" stopColor="#667eea" stopOpacity={0.1} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 12 }}
                  stroke="#666"
                />
                <YAxis
                  tick={{ fontSize: 12 }}
                  stroke="#666"
                  tickFormatter={formatCurrency}
                />
                <Tooltip
                  formatter={(value: number | undefined) => value !== undefined ? formatCurrency(value) : ''}
                  contentStyle={{
                    backgroundColor: 'white',
                    border: '1px solid #e0e0e0',
                    borderRadius: '8px',
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="amount"
                  stroke="#667eea"
                  strokeWidth={3}
                  fill="url(#colorAmount)"
                  dot={{ fill: '#667eea', r: 4 }}
                  activeDot={{ r: 6 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

      {/* Payment Status Distribution Pie Chart */}
      <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom sx={{ fontWeight: 600 }}>
              Payment Status Distribution
            </Typography>
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie
                  data={statusData}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={(entry: any) =>
                    `${entry.name} ${(entry.percent * 100).toFixed(0)}%`
                  }
                  outerRadius={100}
                  fill="#8884d8"
                  dataKey="value"
                >
                  {statusData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'white',
                    border: '1px solid #e0e0e0',
                    borderRadius: '8px',
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

      {/* Top 5 Vendors Bar Chart */}
      <Box sx={{ gridColumn: { xs: '1', md: '1 / -1' } }}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom sx={{ fontWeight: 600 }}>
              Top 5 Vendors by Payment Amount
            </Typography>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={vendorData}>
                <defs>
                  <linearGradient id="colorBar" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#f093fb" stopOpacity={0.9} />
                    <stop offset="95%" stopColor="#f5576c" stopOpacity={0.7} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis
                  dataKey="vendor"
                  tick={{ fontSize: 12 }}
                  stroke="#666"
                />
                <YAxis
                  tick={{ fontSize: 12 }}
                  stroke="#666"
                  tickFormatter={formatCurrency}
                />
                <Tooltip
                  formatter={(value: number | undefined) => value !== undefined ? formatCurrency(value) : ''}
                  contentStyle={{
                    backgroundColor: 'white',
                    border: '1px solid #e0e0e0',
                    borderRadius: '8px',
                  }}
                />
                <Bar
                  dataKey="amount"
                  fill="url(#colorBar)"
                  radius={[8, 8, 0, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </Box>
    </Box>
  );
};
