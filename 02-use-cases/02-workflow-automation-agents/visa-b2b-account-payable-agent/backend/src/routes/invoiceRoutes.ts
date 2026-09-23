import { Router } from 'express';
import {
  list,
  getById,
  create,
  updateStatus,
  linkPo,
  getStats,
  uploadInvoice,
  downloadInvoice,
  downloadIso20022,
  listValidation,
  getByIdValidation,
  createValidation,
  updateStatusValidation,
  linkPoValidation,
  downloadInvoiceValidation,
  downloadIso20022Validation,
} from '../controllers/invoiceController';
import upload from '../middleware/upload';

const router = Router();

// Invoice routes (no auth for POC)
router.get('/', listValidation, list);
router.get('/stats', getStats);
router.get('/:id', getByIdValidation, getById);
router.get('/:id/download', downloadInvoiceValidation, downloadInvoice);
router.get('/:id/download-iso20022', downloadIso20022Validation, downloadIso20022);
router.post('/upload', upload.single('file'), uploadInvoice);
router.patch('/:id/status', updateStatusValidation, updateStatus);
router.post('/:id/link-po', linkPoValidation, linkPo);

// Internal endpoint for Lambda (no auth for POC)
router.post('/', createValidation, create);

export default router;
