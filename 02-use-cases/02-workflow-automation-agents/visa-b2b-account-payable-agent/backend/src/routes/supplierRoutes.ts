import { Router } from 'express';
import {
  login,
  getPaymentDetails,
  getPaymentStatus,
  loginValidation,
  getPaymentValidation,
} from '../controllers/supplierController';

const router = Router();

// Task 7.1: Supplier login endpoint
router.post('/login', loginValidation, login);

// Task 7.2: Supplier payment details endpoint
router.get('/payment/:id', getPaymentValidation, getPaymentDetails);

// Task 7.3: Supplier payment status endpoint
router.get('/payment/:id/status', getPaymentValidation, getPaymentStatus);

export default router;
