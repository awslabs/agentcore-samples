import React, { useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Chip,
  Skeleton,
  Alert,
  Divider,
  Collapse,
  IconButton,
} from '@mui/material';
import {
  Lightbulb as LightbulbIcon,
  ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material';
import type { Recommendation } from '../../types/aiInsights.types';

interface RecommendationsCardProps {
  recommendations: Recommendation[] | null;
  loading: boolean;
  error: string | null;
}

const RecommendationItem: React.FC<{ recommendation: Recommendation }> = ({ recommendation }) => {
  const [expanded, setExpanded] = useState(false);

  const getPriorityColor = (priority: string): string => {
    switch (priority) {
      case 'high':
        return '#f44336';
      case 'medium':
        return '#ff9800';
      case 'low':
        return '#4caf50';
      default:
        return '#9e9e9e';
    }
  };

  const getEffortColor = (effort: string): string => {
    switch (effort) {
      case 'high':
        return '#f44336';
      case 'medium':
        return '#ff9800';
      case 'low':
        return '#4caf50';
      default:
        return '#9e9e9e';
    }
  };

  const getPriorityLabel = (priority: string): string => {
    return priority.charAt(0).toUpperCase() + priority.slice(1);
  };

  return (
    <Box sx={{ mb: 2.5 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
        <Box sx={{ flex: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5, flexWrap: 'wrap' }}>
            <Chip
              label={`${getPriorityLabel(recommendation.priority)} Priority`}
              size="small"
              sx={{
                backgroundColor: getPriorityColor(recommendation.priority),
                color: 'white',
                fontWeight: 600,
                fontSize: '0.7rem',
                height: 22,
              }}
            />
            <Chip
              label={recommendation.category}
              size="small"
              variant="outlined"
              sx={{
                borderColor: 'rgba(255,255,255,0.5)',
                color: 'white',
                fontSize: '0.7rem',
                height: 22,
              }}
            />
          </Box>
          <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
            {recommendation.title}
          </Typography>
          <Typography variant="caption" sx={{ opacity: 0.9, display: 'block', mb: 1 }}>
            {recommendation.description}
          </Typography>
        </Box>
        <IconButton
          onClick={() => setExpanded(!expanded)}
          size="small"
          sx={{
            color: 'white',
            transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
            transition: 'transform 0.3s',
            ml: 1,
          }}
        >
          <ExpandMoreIcon />
        </IconButton>
      </Box>

      <Box sx={{ display: 'flex', gap: 2, mb: 1 }}>
        <Box>
          <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>
            Expected Benefit
          </Typography>
          <Typography variant="caption" sx={{ fontWeight: 600 }}>
            {recommendation.expectedBenefit}
          </Typography>
        </Box>
        <Box>
          <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>
            Effort
          </Typography>
          <Chip
            label={getPriorityLabel(recommendation.effort)}
            size="small"
            sx={{
              backgroundColor: getEffortColor(recommendation.effort),
              color: 'white',
              fontWeight: 600,
              fontSize: '0.65rem',
              height: 20,
            }}
          />
        </Box>
      </Box>

      <Collapse in={expanded}>
        <Box
          sx={{
            mt: 1.5,
            p: 1.5,
            backgroundColor: 'rgba(255, 255, 255, 0.15)',
            borderRadius: 1,
          }}
        >
          <Typography variant="caption" sx={{ fontWeight: 600, display: 'block', mb: 1 }}>
            Implementation Steps:
          </Typography>
          <Box component="ol" sx={{ m: 0, pl: 2.5 }}>
            {recommendation.implementationSteps.map((step, index) => (
              <Typography
                key={index}
                component="li"
                variant="caption"
                sx={{ mb: 0.5, lineHeight: 1.5 }}
              >
                {step}
              </Typography>
            ))}
          </Box>
        </Box>
      </Collapse>
    </Box>
  );
};

export const RecommendationsCard: React.FC<RecommendationsCardProps> = ({
  recommendations,
  loading,
  error,
}) => {
  if (loading) {
    return (
      <Card
        sx={{
          height: '100%',
          background: 'linear-gradient(135deg, #30cfd0 0%, #330867 100%)',
          color: 'white',
        }}
      >
        <CardContent>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 3 }}>
            <LightbulbIcon sx={{ fontSize: 24 }} />
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              Smart Recommendations
            </Typography>
          </Box>
          {[1, 2].map((i) => (
            <Box key={i} sx={{ mb: 2.5 }}>
              <Skeleton variant="text" width="40%" height={24} sx={{ bgcolor: 'rgba(255,255,255,0.2)', mb: 1 }} />
              <Skeleton variant="text" width="90%" height={20} sx={{ bgcolor: 'rgba(255,255,255,0.2)', mb: 0.5 }} />
              <Skeleton variant="text" width="70%" height={20} sx={{ bgcolor: 'rgba(255,255,255,0.2)' }} />
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
            <LightbulbIcon sx={{ fontSize: 24, color: 'primary.main' }} />
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              Smart Recommendations
            </Typography>
          </Box>
          <Alert severity="error">{error}</Alert>
        </CardContent>
      </Card>
    );
  }

  if (!recommendations || recommendations.length === 0) {
    return (
      <Card sx={{ height: '100%' }}>
        <CardContent>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <LightbulbIcon sx={{ fontSize: 24, color: 'primary.main' }} />
            <Typography variant="h6" sx={{ fontWeight: 600 }}>
              Smart Recommendations
            </Typography>
          </Box>
          <Alert severity="info">No recommendations available</Alert>
        </CardContent>
      </Card>
    );
  }

  // Sort by priority
  const sortedRecommendations = [...recommendations].sort((a, b) => {
    const priorityOrder = { high: 1, medium: 2, low: 3 };
    return priorityOrder[a.priority] - priorityOrder[b.priority];
  });

  return (
    <Card
      sx={{
        height: '100%',
        background: 'linear-gradient(135deg, #30cfd0 0%, #330867 100%)',
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
            <LightbulbIcon sx={{ fontSize: 24 }} />
          </Box>
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            Smart Recommendations
          </Typography>
          <Chip
            label={`${recommendations.length} items`}
            size="small"
            sx={{
              ml: 'auto',
              backgroundColor: 'rgba(255, 255, 255, 0.3)',
              color: 'white',
              fontWeight: 600,
            }}
          />
        </Box>

        {/* Recommendations List */}
        {sortedRecommendations.map((recommendation, index) => (
          <React.Fragment key={recommendation.id}>
            <RecommendationItem recommendation={recommendation} />
            {index < sortedRecommendations.length - 1 && (
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
