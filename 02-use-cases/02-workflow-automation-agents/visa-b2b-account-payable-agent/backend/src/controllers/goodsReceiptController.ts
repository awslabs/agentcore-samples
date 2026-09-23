import { Response, NextFunction } from 'express';
import { GoodsReceiptService } from '../services/goodsReceiptService';
import { DocumentJobService } from '../services/documentJobService';
import { JobType } from '../entities/DocumentJob';
import { AuthRequest } from '../middleware/auth';
import { body, query, param, validationResult } from 'express-validator';
import { AppError } from '../middleware/errorHandler';
import { GoodsReceiptStatus } from '../entities/GoodsReceipt';

const grService = new GoodsReceiptService();
const documentJobService = new DocumentJobService();

export const createValidation = [
  body('purchaseOrderId').isUUID().withMessage('Valid purchase order ID required'),
  body('deliveryDate').isISO8601().withMessage('Valid delivery date required'),
  body('quantityReceived').isInt({ min: 1 }).withMessage('Quantity must be positive'),
  body('deliveryReference').trim().notEmpty().withMessage('Delivery reference required'),
  body('conditionNotes').optional().trim(),
];

export const listValidation = [
  query('status').optional().isIn(Object.values(GoodsReceiptStatus)),
  query('page').optional().isInt({ min: 1 }).toInt(),
  query('limit').optional().isInt({ min: 1, max: 100 }).toInt(),
];

export const getByIdValidation = [param('id').isUUID()];

export const create = async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      throw new AppError('Validation failed', 400);
    }

    if (!req.user) {
      throw new AppError('Authentication required', 401);
    }

    const { purchaseOrderId, deliveryDate, quantityReceived, conditionNotes, deliveryReference } =
      req.body;

    const goodsReceipt = await grService.create({
      purchaseOrderId,
      deliveryDate: new Date(deliveryDate),
      quantityReceived,
      conditionNotes,
      deliveryReference,
      receivedById: req.user.userId,
    });

    res.status(201).json(goodsReceipt);
  } catch (error) {
    next(error);
  }
};

export const list = async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      throw new AppError('Validation failed', 400);
    }

    const { status, page, limit } = req.query;

    const result = await grService.findAll({
      status: status as GoodsReceiptStatus,
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
    const gr = await grService.findById(id);

    res.json(gr);
  } catch (error) {
    next(error);
  }
};

// Validation for Lambda-processed GR
export const processFromLambdaValidation = [
  body('extractedData').isObject().withMessage('Extracted data required'),
  body('sourceFileKey').trim().notEmpty().withMessage('Source file key required'),
  body('status').optional().isString(),
  body('needsReview').optional().isBoolean(),
  body('missingFields').optional().isArray(),
  body('lowConfidenceFields').optional().isArray(),
];

// Process GR from Lambda extraction
export const processFromLambda = async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      throw new AppError('Validation failed', 400);
    }

    const { extractedData, sourceFileKey, status, needsReview, missingFields, lowConfidenceFields } = req.body;

    // Map extracted data to GR format
    const grData = {
      bolNumber: extractedData.bol_number || 'UNKNOWN',
      poReference: extractedData.po_reference,
      vendorName: extractedData.vendor?.name,
      deliveryDate: extractedData.delivery_date ? new Date(extractedData.delivery_date) : new Date(),
      materialDescription: extractedData.material_description || '',
      quantityReceived: extractedData.quantity_received || 0,
      unit: extractedData.unit || 'units',
      receivedBy: extractedData.received_by || 'System',
      deliveryLocation: extractedData.delivery_location || '',
      notes: extractedData.notes || '',
      confidenceScores: extractedData.confidence_scores || {},
      sourceFileKey,
      extractedData,
      needsReview: needsReview || false,
      missingFields: missingFields || [],
      lowConfidenceFields: lowConfidenceFields || [],
    };

    const goodsReceipt = await grService.createFromExtraction(grData);

    if (!goodsReceipt) {
      throw new AppError('Failed to create goods receipt', 500);
    }

    res.status(201).json({
      id: goodsReceipt.id,
      bolNumber: grData.bolNumber,
      status: goodsReceipt.status,
      needsReview: grData.needsReview,
      message: needsReview ? 'GR created but needs manual review' : 'GR created successfully',
    });
  } catch (error) {
    next(error);
  }
};

/**
 * Download goods receipt document
 * GET /api/goods-receipts/:id/download
 */
export const downloadDocument = async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const { id } = req.params;

    const goodsReceipt = await grService.findById(id);

    if (!goodsReceipt) {
      throw new AppError('Goods receipt not found', 404);
    }

    if (!goodsReceipt.sourceFileKey) {
      throw new AppError('No document found for this goods receipt', 404);
    }

    // Generate signed S3 URL
    const AWS = require('aws-sdk');
    const s3 = new AWS.S3();
    
    const receiptsBucket = process.env.RECEIPTS_BUCKET;
    
    if (!receiptsBucket) {
      throw new AppError('RECEIPTS_BUCKET environment variable not configured', 500);
    }
    
    const signedUrl = s3.getSignedUrl('getObject', {
      Bucket: receiptsBucket,
      Key: goodsReceipt.sourceFileKey,
      Expires: 3600, // 1 hour
    });

    res.json({
      url: signedUrl,
      fileName: goodsReceipt.sourceFileKey.split('/').pop(),
    });
  } catch (error) {
    next(error);
  }
};

// Get pre-signed URL for direct S3 upload
export const getUploadUrl = async (req: any, res: Response, next: NextFunction) => {
  try {
    const { fileName, fileType } = req.body;
    
    if (!fileName || !fileType) {
      throw new AppError('fileName and fileType are required', 400);
    }

    const AWS = require('aws-sdk');
    const s3 = new AWS.S3({ region: process.env.AWS_REGION || 'us-east-1' });
    
    const inputBucket = process.env.S3_GR_INPUT_BUCKET || process.env.S3_INPUT_BUCKET;
    
    if (!inputBucket) {
      throw new AppError('S3_GR_INPUT_BUCKET environment variable not configured', 500);
    }
    
    // Generate unique key for the file
    const timestamp = Date.now();
    const fileExtension = fileName.split('.').pop()?.toLowerCase();
    const key = `goods-receipts/${timestamp}-${Math.random().toString(36).substring(7)}.${fileExtension}`;
    
    // Create document job
    const job = await documentJobService.createJob({
      jobType: JobType.GOODS_RECEIPT,
      sourceFileKey: key,
    });
    
    // Generate pre-signed URL for upload
    const uploadUrl = s3.getSignedUrl('putObject', {
      Bucket: inputBucket,
      Key: key,
      ContentType: fileType,
      Expires: 300, // 5 minutes
      Metadata: {
        jobId: job.id,
      },
    });
    
    res.status(200).json({
      uploadUrl,
      jobId: job.id,
      fileKey: key,
    });
  } catch (error) {
    next(error);
  }
};
