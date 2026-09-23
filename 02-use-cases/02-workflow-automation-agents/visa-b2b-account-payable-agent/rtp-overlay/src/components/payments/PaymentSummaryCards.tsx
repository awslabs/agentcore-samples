import React from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Skeleton,
} from '@mui/material';
import {
  Payment as PaymentIcon,
  AccountBalance as AccountBalanceIcon,
  Warning as WarningIcon,
  CalendarToday as CalendarIcon,
  TrendingUp as TrendingUpIcon,
  TrendingDown as TrendingDownIcon,
} from '@mui/icons-material';
import type { PaymentSummary } from '../../types/payment.types';

interface PaymentSummaryCardsProps {
  summary: PaymentSummary | null;
  loading: boolean;
}

interface SummaryCardProps {
  title: string;
  value: string | number;
  icon: React.ReactNode;
  gradient: string;
  trend?: {
    direction: 'up' | 'down';
    value: string;
  };
}

const SummaryCard: React.FC<SummaryCardProps> = ({
  title,
  value,
  icon,
  gradient,
  trend,
}) => {
  return (
    <Card
      sx={{
        height: '100%',
        background: gradient,
        color: 'white',
        position: 'relative',
        overflow: 'hidden',
        transition: 'transform 0.2s, box-shadow 0.2s',
        '&:hover': {
          transform: 'translateY(-4px)',
          boxShadow: 6,
        },
      }}
    >
      <CardContent>
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            mb: 2,
          }}
        >
          <Box>
            <Typography
              variant="body2"
              sx={{
                opacity: 0.9,
                fontWeight: 500,
                mb: 1,
              }}
            >
              {title}
            </Typography>
            <Typography
              variant="h4"
              sx={{
                fontWeight: 700,
                mb: 0.5,
              }}
            >
              {value}
            </Typography>
            {trend && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                {trend.direction === 'up' ? (
                  <TrendingUpIcon sx={{ fontSize: 16 }} />
                ) : (
                  <TrendingDownIcon sx={{ fontSize: 16 }} />
                )}
                <Typography variant="caption" sx={{ opacity: 0.9 }}>
                  {trend.value}
                </Typography>
              </Box>
            )}
          </Box>
          <Box
            sx={{
              backgroundColor: 'rgba(255, 255, 255, 0.2)',
              borderRadius: '12px',
              p: 1.5,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            {icon}
          </Box>
        </Box>
      </CardContent>
      {/* Decorative circle */}
      <Box
        sx={{
          position: 'absolute',
          bottom: -30,
          right: -30,
          width: 120,
          height: 120,
          borderRadius: '50%',
          backgroundColor: 'rgba(255, 255, 255, 0.1)',
        }}
      />
    </Card>
  );
};

const LoadingCard: React.FC = () => {
  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Skeleton variant="text" width="60%" height={24} />
        <Skeleton variant="text" width="40%" height={48} sx={{ mt: 1 }} />
        <Skeleton variant="text" width="30%" height={20} sx={{ mt: 0.5 }} />
      </CardContent>
    </Card>
  );
};

export const PaymentSummaryCards: React.FC<PaymentSummaryCardsProps> = ({
  summary,
  loading,
}) => {
  if (loading) {
    return (
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', md: 'repeat(4, 1fr)' },
          gap: 3,
          mb: 4,
        }}
      >
        {[1, 2, 3, 4].map((i) => (
          <LoadingCard key={i} />
        ))}
      </Box>
    );
  }

  if (!summary) {
    return null;
  }

  const formatCurrency = (amount: number): string => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(amount);
  };

  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', md: 'repeat(4, 1fr)' },
        gap: 3,
        mb: 4,
      }}
    >
      <SummaryCard
        title="Total Pending Payments"
        value={summary.totalPendingPayments}
        icon={<PaymentIcon sx={{ fontSize: 32 }} />}
        gradient="linear-gradient(135deg, #667eea 0%, #764ba2 100%)"
      />

      <SummaryCard
        title="Total Payment Amount"
        value={formatCurrency(summary.totalPaymentAmount)}
        icon={<AccountBalanceIcon sx={{ fontSize: 32 }} />}
        gradient="linear-gradient(135deg, #f093fb 0%, #f5576c 100%)"
      />

      <SummaryCard
        title="Overdue Payments"
        value={summary.overduePayments}
        icon={<WarningIcon sx={{ fontSize: 32 }} />}
        gradient="linear-gradient(135deg, #fa709a 0%, #fee140 100%)"
      />

      <SummaryCard
        title="Payments This Week"
        value={summary.paymentsThisWeek}
        icon={<CalendarIcon sx={{ fontSize: 32 }} />}
        gradient="linear-gradient(135deg, #30cfd0 0%, #330867 100%)"
      />
    </Box>
  );
};
