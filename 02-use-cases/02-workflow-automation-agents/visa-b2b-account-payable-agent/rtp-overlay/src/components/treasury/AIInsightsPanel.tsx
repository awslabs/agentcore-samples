import React, { useState, useEffect } from 'react';
import { Box, Typography, IconButton, Alert } from '@mui/material';
import { Refresh as RefreshIcon, AutoAwesome as SparkleIcon } from '@mui/icons-material';
import { aiInsightsService } from '../../services/aiInsightsService';
import { AISummaryCard } from './AISummaryCard';
import { PredictionsCard } from './PredictionsCard';
import { AnomaliesCard } from './AnomaliesCard';
import { RecommendationsCard } from './RecommendationsCard';
import type { AIInsights } from '../../types/aiInsights.types';

export const AIInsightsPanel: React.FC = () => {
  const [insights, setInsights] = useState<AIInsights | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const fetchInsights = async (isRefresh = false) => {
    try {
      if (isRefresh) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      setError(null);

      const data = await aiInsightsService.fetchAllInsights();
      
      // Transform summary string to object format expected by AISummaryCard
      if (typeof data.summary === 'string') {
        data.summary = {
          summary: data.summary,
          generatedAt: data.generatedAt || new Date().toISOString(),
        } as any;
      }
      
      // Transform predictions object to array format expected by PredictionsCard
      if (data.predictions && typeof data.predictions === 'object' && !Array.isArray(data.predictions)) {
        const predictionsObj = data.predictions as any;
        
        // Helper to parse amount - handles both numeric and string formats
        const parseAmount = (amount: any): number => {
          if (typeof amount === 'number') return amount;
          if (typeof amount === 'string') {
            // Remove $, K, M, commas and parse
            const cleaned = amount.replace(/[$,]/g, '');
            if (cleaned.includes('K')) {
              return parseFloat(cleaned.replace('K', '')) * 1000;
            } else if (cleaned.includes('M')) {
              return parseFloat(cleaned.replace('M', '')) * 1000000;
            }
            return parseFloat(cleaned) || 0;
          }
          return 0;
        };
        
        const predictionsArray = [
          {
            period: '7_days' as const,
            predictedAmount: parseAmount(predictionsObj.next7Days?.amount),
            confidence: (predictionsObj.next7Days?.confidence || 0) / 100, // Convert to 0-1 range
            keyFactors: predictionsObj.next7Days?.factors || [],
            assumptions: predictionsObj.assumptions || [],
            risks: predictionsObj.risks || [],
          },
          {
            period: '14_days' as const,
            predictedAmount: parseAmount(predictionsObj.next14Days?.amount),
            confidence: (predictionsObj.next14Days?.confidence || 0) / 100, // Convert to 0-1 range
            keyFactors: predictionsObj.next14Days?.factors || [],
            assumptions: predictionsObj.assumptions || [],
            risks: predictionsObj.risks || [],
          },
          {
            period: '30_days' as const,
            predictedAmount: parseAmount(predictionsObj.next30Days?.amount),
            confidence: (predictionsObj.next30Days?.confidence || 0) / 100, // Convert to 0-1 range
            keyFactors: predictionsObj.next30Days?.factors || [],
            assumptions: predictionsObj.assumptions || [],
            risks: predictionsObj.risks || [],
          },
        ];
        data.predictions = predictionsArray as any;
      }
      
      setInsights(data);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load AI insights';
      setError(errorMessage);
      console.error('Error fetching AI insights:', err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchInsights();

    // Auto-refresh every 15 minutes
    const interval = setInterval(() => {
      fetchInsights(true);
    }, 15 * 60 * 1000);

    return () => clearInterval(interval);
  }, []);

  const handleRefresh = () => {
    fetchInsights(true);
  };

  return (
    <Box sx={{ mt: 4 }}>
      {/* Header */}
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          mb: 3,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Box
            sx={{
              backgroundColor: 'primary.main',
              borderRadius: '12px',
              p: 1,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <SparkleIcon sx={{ fontSize: 28, color: 'white' }} />
          </Box>
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
              AI-Powered Insights
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Real-time treasury analysis powered by Claude Sonnet 4.0
            </Typography>
          </Box>
        </Box>
        <IconButton
          onClick={handleRefresh}
          disabled={refreshing}
          sx={{
            backgroundColor: 'action.hover',
            '&:hover': { backgroundColor: 'action.selected' },
          }}
        >
          <RefreshIcon
            sx={{
              animation: refreshing ? 'spin 1s linear infinite' : 'none',
              '@keyframes spin': {
                '0%': { transform: 'rotate(0deg)' },
                '100%': { transform: 'rotate(360deg)' },
              },
            }}
          />
        </IconButton>
      </Box>

      {/* Global Error */}
      {error && !loading && (
        <Alert severity="error" sx={{ mb: 3 }}>
          {error}
        </Alert>
      )}

      {/* AI Insights Grid */}
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: {
            xs: '1fr',
            md: '1fr 1fr',
            lg: 'repeat(4, 1fr)',
          },
          gap: 3,
        }}
      >
        {/* Summary Card - Full width on mobile, spans 2 columns on desktop */}
        <Box sx={{ gridColumn: { xs: '1', lg: 'span 2' } }}>
          <AISummaryCard
            summary={insights?.summary || null}
            loading={loading}
            error={error}
            onRefresh={handleRefresh}
          />
        </Box>

        {/* Predictions Card - Spans 2 columns on desktop */}
        <Box sx={{ gridColumn: { xs: '1', lg: 'span 2' } }}>
          <PredictionsCard
            predictions={insights?.predictions || null}
            loading={loading}
            error={error}
          />
        </Box>

        {/* Anomalies Card - Spans 2 columns on desktop */}
        <Box sx={{ gridColumn: { xs: '1', lg: 'span 2' } }}>
          <AnomaliesCard
            anomalies={insights?.anomalies || null}
            loading={loading}
            error={error}
          />
        </Box>

        {/* Recommendations Card - Spans 2 columns on desktop */}
        <Box sx={{ gridColumn: { xs: '1', lg: 'span 2' } }}>
          <RecommendationsCard
            recommendations={insights?.recommendations || null}
            loading={loading}
            error={error}
          />
        </Box>
      </Box>
    </Box>
  );
};
