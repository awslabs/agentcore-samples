import { Router } from 'express';
import * as vendorController from '../controllers/vendorController';

const router = Router();

// List vendors - no authentication required for POC
router.get(
  '/',
  vendorController.listValidation,
  vendorController.list
);

// Get vendor by ID - no authentication required for POC
router.get(
  '/:id',
  vendorController.getByIdValidation,
  vendorController.getById
);

export default router;
