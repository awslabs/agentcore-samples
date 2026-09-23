import { Request, Response, NextFunction } from 'express';
import { AuthService } from '../services/authService';
import { body, validationResult } from 'express-validator';
import { AppError } from '../middleware/errorHandler';

const authService = new AuthService();

export const loginValidation = [
  body('username').trim().notEmpty().withMessage('Username is required'),
  body('password').notEmpty().withMessage('Password is required'),
];

export const refreshValidation = [
  body('refreshToken').notEmpty().withMessage('Refresh token is required'),
];

export const login = async (req: Request, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      throw new AppError('Validation failed', 400);
    }

    const { username, password } = req.body;
    const result = await authService.login(username, password);

    res.json(result);
  } catch (error) {
    next(error);
  }
};

export const refresh = async (req: Request, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      throw new AppError('Validation failed', 400);
    }

    const { refreshToken } = req.body;
    const result = await authService.refresh(refreshToken);

    res.json(result);
  } catch (error) {
    next(error);
  }
};

export const logout = async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { refreshToken } = req.body;
    const result = await authService.logout(refreshToken);

    res.json(result);
  } catch (error) {
    next(error);
  }
};
