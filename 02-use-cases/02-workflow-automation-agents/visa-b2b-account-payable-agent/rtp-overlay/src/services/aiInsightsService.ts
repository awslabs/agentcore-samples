/**
 * AI Insights Service
 * Handles API calls to the backend for AI-generated treasury insights
 */

import type {
  AIInsights,
  AISummary,
  CashFlowPrediction,
  Anomaly,
  Recommendation,
  AIInsightsError,
} from '../types/aiInsights.types';

class AIInsightsService {
  private baseURL = `${import.meta.env.VITE_API_BASE_URL || 'https://il9nu3s9c3.execute-api.us-east-1.amazonaws.com/prod'}/api/ai`;

  /**
   * Fetch all AI insights (summary, predictions, anomalies, recommendations)
   */
  async fetchAllInsights(): Promise<AIInsights> {
    const response = await fetch(`${this.baseURL}/insights`, {
      headers: this.getAuthHeaders(),
    });

    if (!response.ok) {
      throw await this.handleError(response);
    }

    return response.json();
  }

  /**
   * Fetch AI-generated treasury summary
   */
  async fetchSummary(): Promise<AISummary> {
    const response = await fetch(`${this.baseURL}/summary`, {
      headers: this.getAuthHeaders(),
    });

    if (!response.ok) {
      throw await this.handleError(response);
    }

    return response.json();
  }

  /**
   * Fetch cash flow predictions (7, 14, 30 days)
   */
  async fetchPredictions(): Promise<CashFlowPrediction[]> {
    const response = await fetch(`${this.baseURL}/predictions`, {
      headers: this.getAuthHeaders(),
    });

    if (!response.ok) {
      throw await this.handleError(response);
    }

    return response.json();
  }

  /**
   * Fetch detected anomalies
   */
  async fetchAnomalies(): Promise<Anomaly[]> {
    const response = await fetch(`${this.baseURL}/anomalies`, {
      headers: this.getAuthHeaders(),
    });

    if (!response.ok) {
      throw await this.handleError(response);
    }

    return response.json();
  }

  /**
   * Fetch smart recommendations
   */
  async fetchRecommendations(): Promise<Recommendation[]> {
    const response = await fetch(`${this.baseURL}/recommendations`, {
      headers: this.getAuthHeaders(),
    });

    if (!response.ok) {
      throw await this.handleError(response);
    }

    return response.json();
  }

  /**
   * Refresh all insights (triggers new AI generation)
   * This is the same as fetchAllInsights but semantically indicates a refresh action
   */
  async refreshInsights(): Promise<AIInsights> {
    return this.fetchAllInsights();
  }

  /**
   * Get authentication headers with JWT token
   */
  private getAuthHeaders(): HeadersInit {
    const token = localStorage.getItem('accessToken');
    return {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    };
  }

  /**
   * Handle API errors and return structured error
   */
  private async handleError(response: Response): Promise<Error> {
    let errorMessage = `AI Insights API error: ${response.statusText}`;
    
    try {
      const errorData: AIInsightsError = await response.json();
      errorMessage = errorData.message || errorMessage;
    } catch {
      // If response is not JSON, use status text
    }

    return new Error(errorMessage);
  }
}

export const aiInsightsService = new AIInsightsService();
