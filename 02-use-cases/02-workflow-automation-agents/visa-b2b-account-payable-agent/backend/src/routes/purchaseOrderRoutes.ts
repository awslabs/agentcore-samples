import { Router } from 'express';
import * as poController from '../controllers/purchaseOrderController';

const router = Router();

// List purchase orders - no authentication required
router.get('/', poController.getPurchaseOrders);

// Get purchase order by ID
router.get('/:id', poController.getPurchaseOrderById);

// Create purchase order
router.post('/', poController.createPurchaseOrder);

// Database status endpoint (for debugging)
router.get('/status/database', poController.getDatabaseStatus);

export default router;
