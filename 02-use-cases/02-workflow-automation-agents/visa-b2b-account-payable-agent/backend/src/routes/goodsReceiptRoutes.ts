import { Router } from 'express';
import * as grController from '../controllers/goodsReceiptController';
import * as documentController from '../controllers/receiptDocumentController';
import upload from '../middleware/upload';
import { uploadGoodsReceipt } from '../controllers/goodsReceiptUploadController';

const router = Router();

// Goods Receipt routes (no auth for POC)
router.post(
  '/upload',
  upload.single('file'),
  uploadGoodsReceipt
);

router.post(
  '/process',
  grController.processFromLambdaValidation,
  grController.processFromLambda
);

router.post(
  '/upload-url',
  grController.getUploadUrl
);

router.post(
  '/',
  grController.createValidation,
  grController.create
);

router.get(
  '/',
  grController.listValidation,
  grController.list
);

router.get(
  '/:id',
  grController.getByIdValidation,
  grController.getById
);

router.get(
  '/:id/download',
  grController.downloadDocument
);

router.post(
  '/:id/documents',
  upload.array('files', 5),
  documentController.uploadValidation,
  documentController.upload
);

router.get(
  '/documents/:documentId/url',
  documentController.getDocumentUrl
);

export default router;
