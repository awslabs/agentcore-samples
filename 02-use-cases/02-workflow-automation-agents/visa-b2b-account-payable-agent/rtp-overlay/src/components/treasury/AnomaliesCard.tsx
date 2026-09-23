import React from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Chip,
  Skeleton,
  Alert,
  Divider,
} from '@mui/material';
import {
  Warning as WarningIcon,
  CheckCircle as CheckCircleIcon,
} from '@mui/icons-material';
import type { Anomaly } from '../../types/aiInsights.types';

interface AnomaliesCardProps {
  anomalies: Anomaly[] | null;
  loading: boolean;
  error: string | null;
}

const AnomalyItem: React.FC<{ anomaly: Anomaly }> = ({ anomaly }) => {
  const getSeverityColor = (severity: string): string => {
    switch (severity) {
      case 'high':
        return '#f44336';
      case 'medium':
        return '#ff9800';
      case 'low':
        return '#ffc107';
      default:
        return '#9e9e9e';
    }
  };

  const getSeverityLabel = (severity: string): string => {
    return severity.charAt(0).toUpperCase() + severity.slice(1);
  };

  return (
    <Box sx={{ mb: 2.5 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
        <Box sx={{ flex: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
            <Chip
              label={getSeverityLabel(anomaly.severity)}
              size="small"
              sx={{
                backgroundColor: getSeverityColor(anomaly.severity),
                color: 'white',
                fontWeight: 600,
                fontSize: '0.7rem',
                height: 22,
              }}
            />
            <Typography variant="caption" color="text.secondary">
              {anomaly.type}
            </Typography>
          </Box>
          <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
            {anomaly.description}
          </Typography>
        </Box>
        <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
          {Math.round(anomaly.confidence * 100)}%
        </Typography>
      </Box>

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
        <strong>Affected:</strong> {anomaly.affectedEntity}
      </Typography>

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
        <strong>Impact:</strong> {anomaly.impact}
      </Typography>

      <Box
        sx={{
          mt: 1,
          p: 1,
          backgroundColor: 'action.hover',
          borderRadius: 1,
          borderLeft: `3px solid ${getSeverityColor(anomaly.severity)}`,
        }}
      >
        <Typography variant="caption" sx={{ fontWeight: 600, display: 'block', mb: 0.5 }}>
          Recommendation:
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {anomaly.recommendation}
        </Typography>
      </Box>
    </Box>
  );
};

export const AnomaliesCard: React.FC<AnomaliesCardProps> = ({
  anomalies,
  loading,
  error,
}) => {
  if (loading) {
    return (
      <Card
        sx={{
          height: '100%',
          background: 'linear-gradient(135deg, #fa709a 0%, #fee140 100%)',
          color: 'white',
        }}
      >
        <CardContent>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 3 }}>
            <WarningIcon sx={{ fontSize: 24 }} />
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              Anomaly Detection
            </Typography>
          </Box>
          {[1, 2].map((i) => (
            <Box key={i} sx={{ mb: 2.5 }}>
              <Skeleton variant="text" width="30%" height={24} sx={{ bgcolor: 'rgba(255,255,255,0.2)', mb: 1 }} />
              <Skeleton variant="text" width="90%" height={20} sx={{ bgcolor: 'rgba(255,255,255,0.2)', mb: 0.5 }} />
              <Skeleton variant="rectangular" width="100%" height={60} sx={{ bgcolor: 'rgba(255,255,255,0.2)', borderRadius: 1 }} />
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
            <WarningIcon sx={{ fontSize: 24, color: 'primary.main' }} />
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              Anomaly Detection
            </Typography>
          </Box>
          <Alert severity="error">{error}</Alert>
        </CardContent>
      </Card>
    );
  }

  if (!anomalies || anomalies.length === 0) {
    return (
      <Card
        sx={{
          height: '100%',
          background: 'linear-gradient(135deg, #a8edea 0%, #fed6e3 100%)',
          color: 'text.primary',
        }}
      >
        <CardContent>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <Box
              sx={{
                backgroundColor: 'rgba(76, 175, 80, 0.2)',
                borderRadius: '8px',
                p: 0.5,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <CheckCircleIcon sx={{ fontSize: 24, color: '#4caf50' }} />
            </Box>
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              Anomaly Detection
            </Typography>
          </Box>
          <Box
            sx={{
              textAlign: 'center',
              py: 4,
            }}
          >
            <CheckCircleIcon sx={{ fontSize: 48, color: '#4caf50', mb: 2 }} />
            <Typography variant="body1" sx={{ fontWeight: 600, mb: 1 }}>
              No Anomalies Detected
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Your treasury operations are running smoothly
            </Typography>
          </Box>
        </CardContent>
      </Card>
    );
  }

  // Sort by severity
  const sortedAnomalies = [...anomalies].sort((a, b) => {
    const severityOrder = { high: 1, medium: 2, low: 3 };
    return severityOrder[a.severity] - severityOrder[b.severity];
  });

  return (
    <Card
      sx={{
        height: '100%',
        background: 'linear-gradient(135deg, #fa709a 0%, #fee140 100%)',
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
            <WarningIcon sx={{ fontSize: 24 }} />
          </Box>
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            Anomaly Detection
          </Typography>
          <Chip
            label={`${anomalies.length} found`}
            size="small"
            sx={{
              ml: 'auto',
              backgroundColor: 'rgba(255, 255, 255, 0.3)',
              color: 'white',
              fontWeight: 600,
            }}
          />
        </Box>

        {/* Anomalies List */}
        {sortedAnomalies.map((anomaly, index) => (
          <React.Fragment key={anomaly.id}>
            <AnomalyItem anomaly={anomaly} />
            {index < sortedAnomalies.length - 1 && (
              <Divider sx={{ my: 2, borderColor: 'rgba(255,255,255,0.2)' }} />
            )}
          </React.Fragment>
        ))}
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
