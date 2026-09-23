import { Router } from 'express';
import {
  list,
  getById,
  getSummary,
  getISO20022File,
  processPayment,
  getCardDetails,
  listValidation,
  getByIdValidation,
  downloadFileValidation,
  processPaymentValidation,
} from '../controllers/paymentController';

const router = Router();

// Payment routes
router.get('/', listValidation, list);
router.get('/summary', getSummary);
router.get('/:id', getByIdValidation, getById);
router.get('/:id/iso20022-file', downloadFileValidation, getISO20022File);

// Task 6.2: Process payment (invoke Payment Agent)
router.post('/process', processPaymentValidation, processPayment);

// Task 6.3: Get decrypted card details
router.get('/:id/card-details', getByIdValidation, getCardDetails);

export default router;
