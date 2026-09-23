import { Request, Response, NextFunction } from 'express';
import { verifyAccessToken, JwtPayload } from '../utils/jwt';
import { AppError } from './errorHandler';
import { UserRole } from '../entities/User';

export interface AuthRequest extends Request {
  user?: JwtPayload;
}

export const authenticate = (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const authHeader = req.headers.authorization;

    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      throw new AppError('No token provided', 401);
    }

    const token = authHeader.substring(7);
    
    // Development-only: Accept static token (REMOVE IN PRODUCTION)
    // Only enabled when NODE_ENV is explicitly set to 'development'
    if (process.env.NODE_ENV === 'development' && process.env.ENABLE_STATIC_TOKEN === 'true') {
      const STATIC_TOKEN = process.env.STATIC_DEV_TOKEN;
      if (STATIC_TOKEN && token === STATIC_TOKEN) {
        req.user = {
          userId: 'dev-user',
          username: 'developer',
          role: 'admin' as UserRole,
        };
        next();
        return;
      }
    }
    
    const payload = verifyAccessToken(token);

    req.user = payload;
    next();
  } catch (error) {
    if (error instanceof AppError) {
      next(error);
    } else {
      next(new AppError('Invalid or expired token', 401));
    }
  }
};

export const authorize = (...roles: UserRole[]) => {
  return (req: AuthRequest, res: Response, next: NextFunction) => {
    if (!req.user) {
      return next(new AppError('Authentication required', 401));
    }

    if (!roles.includes(req.user.role as UserRole)) {
      return next(new AppError('Insufficient permissions', 403));
    }

    next();
  };
};
