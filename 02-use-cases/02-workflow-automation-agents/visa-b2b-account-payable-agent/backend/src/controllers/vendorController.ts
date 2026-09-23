import { Response, NextFunction } from 'express';
import { VendorService } from '../services/vendorService';
import { AuthRequest } from '../middleware/auth';
import { query, param, validationResult } from 'express-validator';
import { AppError } from '../middleware/errorHandler';

const vendorService = new VendorService();

export const listValidation = [
  query('is_active').optional().isBoolean().toBoolean(),
  query('page').optional().isInt({ min: 1 }).toInt(),
  query('limit').optional().isInt({ min: 1, max: 100 }).toInt(),
];

export const getByIdValidation = [param('id').isUUID()];

export const list = async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      throw new AppError('Validation failed', 400);
    }

    const { is_active, page, limit } = req.query;

    const result = await vendorService.findAll({
      isActive: is_active !== undefined ? (is_active === 'true') : undefined,
      page: page ? parseInt(page as string) : undefined,
      limit: limit ? parseInt(limit as string) : undefined,
    });

    res.json(result);
  } catch (error) {
    next(error);
  }
};

export const getById = async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      throw new AppError('Validation failed', 400);
    }

    const { id } = req.params;
    const vendor = await vendorService.findById(id);

    res.json(vendor);
  } catch (error) {
    next(error);
  }
};
