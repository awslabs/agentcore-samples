import React from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  IconButton,
  Skeleton,
  Alert,
} from '@mui/material';
import {
  AutoAwesome as SparkleIcon,
  Refresh as RefreshIcon,
} from '@mui/icons-material';
import type { AISummary } from '../../types/aiInsights.types';

interface AISummaryCardProps {
  summary: AISummary | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}

export const AISummaryCard: React.FC<AISummaryCardProps> = ({
  summary,
  loading,
  error,
  onRefresh,
}) => {
  const formatTimestamp = (timestamp: string | undefined): string => {
    if (!timestamp) {
      return 'Just now';
    }
    
    try {
      const date = new Date(timestamp);
      if (isNaN(date.getTime())) {
        return 'Just now';
      }
      return new Intl.DateTimeFormat('en-US', {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      }).format(date);
    } catch {
      return 'Just now';
    }
  };

  if (loading) {
    return (
      <Card
        sx={{
          height: '100%',
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
          color: 'white',
        }}
      >
        <CardContent>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <SparkleIcon sx={{ fontSize: 24 }} />
              <Typography variant="h6" sx={{ fontWeight: 600 }}>
                AI Treasury Summary
              </Typography>
            </Box>
            <Skeleton variant="circular" width={40} height={40} sx={{ bgcolor: 'rgba(255,255,255,0.2)' }} />
          </Box>
          <Skeleton variant="text" width="100%" height={24} sx={{ bgcolor: 'rgba(255,255,255,0.2)', mb: 1 }} />
          <Skeleton variant="text" width="90%" height={24} sx={{ bgcolor: 'rgba(255,255,255,0.2)', mb: 1 }} />
          <Skeleton variant="text" width="95%" height={24} sx={{ bgcolor: 'rgba(255,255,255,0.2)', mb: 1 }} />
          <Skeleton variant="text" width="60%" height={20} sx={{ bgcolor: 'rgba(255,255,255,0.2)', mt: 2 }} />
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card sx={{ height: '100%' }}>
        <CardContent>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <SparkleIcon sx={{ fontSize: 24, color: 'primary.main' }} />
              <Typography variant="h6" sx={{ fontWeight: 600 }}>
                AI Treasury Summary
              </Typography>
            </Box>
            <IconButton
              onClick={onRefresh}
              size="small"
              sx={{
                backgroundColor: 'action.hover',
                '&:hover': { backgroundColor: 'action.selected' },
              }}
            >
              <RefreshIcon />
            </IconButton>
          </Box>
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        </CardContent>
      </Card>
    );
  }

  if (!summary) {
    return null;
  }

  return (
    <Card
      sx={{
        height: '100%',
        background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
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
        {/* Header */}
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            mb: 2,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
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
              <SparkleIcon sx={{ fontSize: 24 }} />
            </Box>
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              AI Treasury Summary
            </Typography>
          </Box>
          <IconButton
            onClick={onRefresh}
            size="small"
            sx={{
              color: 'white',
              backgroundColor: 'rgba(255, 255, 255, 0.2)',
              '&:hover': {
                backgroundColor: 'rgba(255, 255, 255, 0.3)',
              },
            }}
          >
            <RefreshIcon />
          </IconButton>
        </Box>

        {/* Summary Text */}
        <Typography
          variant="body1"
          sx={{
            lineHeight: 1.7,
            opacity: 0.95,
            mb: 2,
          }}
        >
          {summary.summary}
        </Typography>

        {/* Last Updated */}
        <Typography
          variant="caption"
          sx={{
            opacity: 0.8,
            fontStyle: 'italic',
          }}
        >
          Last updated: {formatTimestamp(summary.generatedAt)}
        </Typography>
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
      <Box
        sx={{
          position: 'absolute',
          top: -20,
          left: -20,
          width: 100,
          height: 100,
          borderRadius: '50%',
          backgroundColor: 'rgba(255, 255, 255, 0.05)',
        }}
      />
    </Card>
  );
};
