import React from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  LinearProgress,
  Chip,
  Skeleton,
  Alert,
  Divider,
} from '@mui/material';
import {
  TrendingUp as TrendingUpIcon,
  Info as InfoIcon,
} from '@mui/icons-material';
import type { CashFlowPrediction } from '../../types/aiInsights.types';

interface PredictionsCardProps {
  predictions: CashFlowPrediction[] | null;
  loading: boolean;
  error: string | null;
}

const PredictionItem: React.FC<{ prediction: CashFlowPrediction }> = ({ prediction }) => {
  const formatCurrency = (amount: number): string => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(amount);
  };

  const getPeriodLabel = (period: string): string => {
    switch (period) {
      case '7_days':
        return '7 Days';
      case '14_days':
        return '14 Days';
      case '30_days':
        return '30 Days';
      default:
        return period;
    }
  };

  const getConfidenceColor = (confidence: number): string => {
    if (confidence >= 0.8) return '#4caf50';
    if (confidence >= 0.6) return '#ff9800';
    return '#f44336';
  };

  return (
    <Box sx={{ mb: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
          {getPeriodLabel(prediction.period)}
        </Typography>
        <Typography variant="h6" sx={{ fontWeight: 700, color: 'primary.main' }}>
          {formatCurrency(prediction.predictedAmount)}
        </Typography>
      </Box>

      {/* Confidence Bar */}
      <Box sx={{ mb: 1.5 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 0.5 }}>
          <Typography variant="caption" color="text.secondary">
            Confidence
          </Typography>
          <Typography variant="caption" sx={{ fontWeight: 600 }}>
            {Math.round(prediction.confidence * 100)}%
          </Typography>
        </Box>
        <LinearProgress
          variant="determinate"
          value={prediction.confidence * 100}
          sx={{
            height: 8,
            borderRadius: 4,
            backgroundColor: 'rgba(0,0,0,0.1)',
            '& .MuiLinearProgress-bar': {
              backgroundColor: getConfidenceColor(prediction.confidence),
              borderRadius: 4,
            },
          }}
        />
      </Box>

      {/* Key Factors */}
      {prediction.keyFactors && prediction.keyFactors.length > 0 && (
        <Box sx={{ mb: 1 }}>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600, mb: 0.5, display: 'block' }}>
            Key Factors:
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
            {prediction.keyFactors.slice(0, 3).map((factor, index) => (
              <Chip
                key={index}
                label={factor}
                size="small"
                sx={{
                  fontSize: '0.7rem',
                  height: 24,
                  backgroundColor: 'action.hover',
                }}
              />
            ))}
          </Box>
        </Box>
      )}
    </Box>
  );
};

export const PredictionsCard: React.FC<PredictionsCardProps> = ({
  predictions,
  loading,
  error,
}) => {
  if (loading) {
    return (
      <Card
        sx={{
          height: '100%',
          background: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)',
          color: 'white',
        }}
      >
        <CardContent>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 3 }}>
            <TrendingUpIcon sx={{ fontSize: 24 }} />
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              Cash Flow Predictions
            </Typography>
          </Box>
          {[1, 2, 3].map((i) => (
            <Box key={i} sx={{ mb: 3 }}>
              <Skeleton variant="text" width="40%" height={24} sx={{ bgcolor: 'rgba(255,255,255,0.2)', mb: 1 }} />
              <Skeleton variant="rectangular" width="100%" height={8} sx={{ bgcolor: 'rgba(255,255,255,0.2)', mb: 1, borderRadius: 4 }} />
              <Skeleton variant="text" width="60%" height={20} sx={{ bgcolor: 'rgba(255,255,255,0.2)' }} />
            </Box>
          ))}
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card sx={{ height: '100%' }}>
        <CardContent>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <TrendingUpIcon sx={{ fontSize: 24, color: 'primary.main' }} />
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              Cash Flow Predictions
            </Typography>
          </Box>
          <Alert severity="error">{error}</Alert>
        </CardContent>
      </Card>
    );
  }

  if (!predictions || predictions.length === 0) {
    return (
      <Card sx={{ height: '100%' }}>
        <CardContent>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <TrendingUpIcon sx={{ fontSize: 24, color: 'primary.main' }} />
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              Cash Flow Predictions
            </Typography>
          </Box>
          <Alert severity="info">No predictions available</Alert>
        </CardContent>
      </Card>
    );
  }

  // Sort predictions by period
  const sortedPredictions = [...predictions].sort((a, b) => {
    const order = { '7_days': 1, '14_days': 2, '30_days': 3 };
    return order[a.period] - order[b.period];
  });

  // Get assumptions and risks from first prediction (they're usually the same across all)
  const firstPrediction = sortedPredictions[0];
  const hasAssumptions = firstPrediction.assumptions && firstPrediction.assumptions.length > 0;
  const hasRisks = firstPrediction.risks && firstPrediction.risks.length > 0;

  return (
    <Card
      sx={{
        height: '100%',
        background: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)',
        color: 'white',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <CardContent>
        {/* Header */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 3 }}>
          <Box
            sx={{
              backgroundColor: 'rgba(255, 255, 255, 0.2)',
              borderRadius: '8px',
              p: 0.5,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <TrendingUpIcon sx={{ fontSize: 24 }} />
          </Box>
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            Cash Flow Predictions
          </Typography>
        </Box>

        {/* Predictions */}
        {sortedPredictions.map((prediction, index) => (
          <React.Fragment key={prediction.period}>
            <PredictionItem prediction={prediction} />
            {index < sortedPredictions.length - 1 && (
              <Divider sx={{ my: 2, borderColor: 'rgba(255,255,255,0.2)' }} />
            )}
          </React.Fragment>
        ))}

        {/* Assumptions & Risks */}
        {(hasAssumptions || hasRisks) && (
          <Box
            sx={{
              mt: 3,
              pt: 2,
              borderTop: '1px solid rgba(255,255,255,0.2)',
            }}
          >
            {hasAssumptions && (
              <Box sx={{ mb: 2 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
                  <InfoIcon sx={{ fontSize: 16, opacity: 0.8 }} />
                  <Typography variant="caption" sx={{ fontWeight: 600, opacity: 0.9 }}>
                    Assumptions:
                  </Typography>
                </Box>
                <Typography variant="caption" sx={{ opacity: 0.85, lineHeight: 1.5, display: 'block' }}>
                  {firstPrediction.assumptions.join('; ')}
                </Typography>
              </Box>
            )}

            {hasRisks && (
              <Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
                  <InfoIcon sx={{ fontSize: 16, opacity: 0.8 }} />
                  <Typography variant="caption" sx={{ fontWeight: 600, opacity: 0.9 }}>
                    Risks:
                  </Typography>
                </Box>
                <Typography variant="caption" sx={{ opacity: 0.85, lineHeight: 1.5, display: 'block' }}>
                  {firstPrediction.risks.join('; ')}
                </Typography>
              </Box>
            )}
          </Box>
        )}
      </CardContent>

      {/* Decorative elements */}
      <Box
        sx={{
          position: 'absolute',
          bottom: -40,
          right: -40,
          width: 150,
          height: 150,
          borderRadius: '50%',
          backgroundColor: 'rgba(255, 255, 255, 0.1)',
        }}
      />
    </Card>
  );
};
