import { Router } from 'express';
import { clearData, clearInvoicesAndGR } from '../controllers/adminController';
import { authenticate } from '../middleware/auth';

const router = Router();

// Clear all data (authenticated users only)
router.delete('/clear-data', authenticate, clearData);

// Clear only invoices and goods receipts (keep POs, vendors, users)
router.delete('/clear-invoices-gr', authenticate, clearInvoicesAndGR);

export default router;
