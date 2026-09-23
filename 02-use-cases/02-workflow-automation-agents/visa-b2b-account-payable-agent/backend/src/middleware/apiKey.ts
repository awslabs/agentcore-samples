import { Request, Response, NextFunction } from 'express';
import { AppError } from './errorHandler';

/**
 * Middleware to authenticate requests using X-API-Key header
 * Used for internal Lambda-to-API communication
 */
export const authenticateApiKey = (
  req: Request,
  res: Response,
  next: NextFunction
): void => {
  try {
    const apiKey = req.headers['x-api-key'] as string;

    if (!apiKey) {
      throw new AppError('API key required', 401);
    }

    // Get expected API key from environment
    const expectedApiKey = process.env.LAMBDA_API_KEY;

    if (!expectedApiKey) {
      console.error('LAMBDA_API_KEY not configured in environment');
      throw new AppError('API key authentication not configured', 500);
    }

    if (apiKey !== expectedApiKey) {
      throw new AppError('Invalid API key', 401);
    }

    // API key is valid, continue
    next();
  } catch (error) {
    next(error);
  }
};
