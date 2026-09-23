/**
 * AI Insights Types
 * Type definitions for AI-generated treasury insights
 */

export interface AISummary {
  summary: string;
  generatedAt: string;
}

export interface CashFlowPrediction {
  period: '7_days' | '14_days' | '30_days';
  predictedAmount: number;
  confidence: number;
  keyFactors: string[];
  assumptions: string[];
  risks: string[];
}

export interface Anomaly {
  id: string;
  type: string;
  severity: 'high' | 'medium' | 'low';
  description: string;
  affectedEntity: string;
  impact: string;
  recommendation: string;
  confidence: number;
  detectedAt: string;
}

export interface Recommendation {
  id: string;
  title: string;
  description: string;
  priority: 'high' | 'medium' | 'low';
  expectedBenefit: string;
  implementationSteps: string[];
  effort: 'low' | 'medium' | 'high';
  category: string;
}

export interface AIInsights {
  summary: AISummary;
  predictions: CashFlowPrediction[];
  anomalies: Anomaly[];
  recommendations: Recommendation[];
  generatedAt: string;
}

export interface AIInsightsError {
  error: string;
  message: string;
  timestamp: string;
}
