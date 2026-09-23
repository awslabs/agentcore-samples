import { Request, Response, NextFunction } from 'express';
import { logger } from '../utils/logger';

export class AppError extends Error {
  statusCode: number;
  isOperational: boolean;

  constructor(message: string, statusCode: number) {
    super(message);
    this.statusCode = statusCode;
    this.isOperational = true;
    Error.captureStackTrace(this, this.constructor);
  }
}

export const errorHandler = (
  err: Error | AppError,
  req: Request,
  res: Response,
  next: NextFunction
) => {
  if (err instanceof AppError) {
    logger.error(`${err.statusCode} - ${err.message} - ${req.originalUrl} - ${req.method}`);
    
    const errorResponse: any = {
      error: {
        code: err.statusCode,
        message: err.message,
        timestamp: new Date().toISOString(),
        path: req.originalUrl,
      },
    };
    
    // Include additional properties (like invoiceId for duplicates)
    if ((err as any).invoiceId) {
      errorResponse.invoiceId = (err as any).invoiceId;
    }
    
    return res.status(err.statusCode).json(errorResponse);
  }

  // Unexpected errors
  logger.error(`500 - ${err.message} - ${req.originalUrl} - ${req.method}`, { stack: err.stack });
  
  return res.status(500).json({
    error: {
      code: 500,
      message: 'Internal server error',
      timestamp: new Date().toISOString(),
      path: req.originalUrl,
    },
  });
};
