import { Request, Response } from 'express';
import AWS from 'aws-sdk';
import { logger } from '../utils/logger';

const lambda = new AWS.Lambda({
  region: process.env.AWS_REGION || 'us-east-1'
});

const AI_INSIGHTS_LAMBDA_ARN = process.env.AI_INSIGHTS_LAMBDA_ARN || 'ai-insights-lambda';

export class AIInsightsController {
  /**
   * GET /api/ai/insights
   * Get all AI insights (summary, predictions, anomalies, recommendations)
   */
  async getAllInsights(req: Request, res: Response): Promise<Response> {
    try {
      const userId = (req as any).user?.id;
      
      logger.info('Fetching all AI insights', { userId });
      
      const result = await this.invokeLambda('all', userId);
      
      return res.json(result);
    } catch (error) {
      logger.error('Failed to get all AI insights:', error);
      return res.status(500).json({
        error: 'Failed to generate AI insights',
        message: error instanceof Error ? error.message : 'Unknown error'
      });
    }
  }

  /**
   * GET /api/ai/summary
   * Get AI-generated treasury summary
   */
  async getSummary(req: Request, res: Response): Promise<Response> {
    try {
      const userId = (req as any).user?.id;
      
      logger.info('Fetching AI summary', { userId });
      
      const result = await this.invokeLambda('summary', userId);
      
      return res.json(result);
    } catch (error) {
      logger.error('Failed to get AI summary:', error);
      return res.status(500).json({
        error: 'Failed to generate summary',
        message: error instanceof Error ? error.message : 'Unknown error'
      });
    }
  }

  /**
   * GET /api/ai/predictions
   * Get cash flow predictions
   */
  async getPredictions(req: Request, res: Response): Promise<Response> {
    try {
      const userId = (req as any).user?.id;
      
      logger.info('Fetching cash flow predictions', { userId });
      
      const result = await this.invokeLambda('predictions', userId);
      
      return res.json(result);
    } catch (error) {
      logger.error('Failed to get predictions:', error);
      return res.status(500).json({
        error: 'Failed to generate predictions',
        message: error instanceof Error ? error.message : 'Unknown error'
      });
    }
  }

  /**
   * GET /api/ai/anomalies
   * Get detected anomalies
   */
  async getAnomalies(req: Request, res: Response): Promise<Response> {
    try {
      const userId = (req as any).user?.id;
      
      logger.info('Fetching anomalies', { userId });
      
      const result = await this.invokeLambda('anomalies', userId);
      
      return res.json(result);
    } catch (error) {
      logger.error('Failed to get anomalies:', error);
      return res.status(500).json({
        error: 'Failed to detect anomalies',
        message: error instanceof Error ? error.message : 'Unknown error'
      });
    }
  }

  /**
   * GET /api/ai/recommendations
   * Get AI recommendations
   */
  async getRecommendations(req: Request, res: Response): Promise<Response> {
    try {
      const userId = (req as any).user?.id;
      
      logger.info('Fetching recommendations', { userId });
      
      const result = await this.invokeLambda('recommendations', userId);
      
      return res.json(result);
    } catch (error) {
      logger.error('Failed to get recommendations:', error);
      return res.status(500).json({
        error: 'Failed to generate recommendations',
        message: error instanceof Error ? error.message : 'Unknown error'
      });
    }
  }

  /**
   * Private helper to invoke Lambda
   */
  private async invokeLambda(insightType: string, userId?: string): Promise<any> {
    try {
      const payload = {
        insightType,
        userId
      };

      logger.info('Invoking AI Insights Lambda', { 
        functionName: AI_INSIGHTS_LAMBDA_ARN,
        payload 
      });

      const result = await lambda.invoke({
        FunctionName: AI_INSIGHTS_LAMBDA_ARN,
        InvocationType: 'RequestResponse',
        Payload: JSON.stringify(payload)
      }).promise();

      if (!result.Payload) {
        throw new Error('No response from Lambda');
      }

      const response = JSON.parse(result.Payload.toString());

      // Check for Lambda errors
      if (response.statusCode !== 200) {
        const errorBody = JSON.parse(response.body);
        throw new Error(errorBody.error || 'Lambda execution failed');
      }

      // Parse the body
      const insights = JSON.parse(response.body);

      logger.info('Successfully received AI insights from Lambda', {
        insightType,
        hasData: !!insights
      });

      return insights;
    } catch (error) {
      logger.error('Lambda invocation failed:', error);
      throw error;
    }
  }
}

export const aiInsightsController = new AIInsightsController();
