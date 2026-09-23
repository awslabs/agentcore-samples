import { Request, Response, NextFunction } from 'express';
import { InvoiceService } from '../services/invoiceService';
import { DocumentJobService } from '../services/documentJobService';
import { JobType } from '../entities/DocumentJob';
import { query, param, body, validationResult } from 'express-validator';
import { AppError } from '../middleware/errorHandler';
import { PaymentStatus } from '../entities/Invoice';

const invoiceService = new InvoiceService();
const documentJobService = new DocumentJobService();

// Validation rules
export const listValidation = [
  query('status').optional().isIn(Object.values(PaymentStatus)),
  query('vendor_id').optional().isUUID(),
  query('has_po_match').optional().isBoolean().toBoolean(),
  query('has_variance_warning').optional().isBoolean().toBoolean(),
  query('start_date').optional().isISO8601(),
  query('end_date').optional().isISO8601(),
  query('page').optional().isInt({ min: 1 }).toInt(),
  query('limit').optional().isInt({ min: 1, max: 100 }).toInt(),
  query('sort_by').optional().isIn(['invoice_date', 'total_amount', 'created_at']),
  query('sort_order').optional().isIn(['ASC', 'DESC']),
];

export const getByIdValidation = [param('id').isUUID()];

export const createValidation = [
  body('extractedData').exists(),
  body('extractedData.supplier.name').notEmpty(),
  body('extractedData.invoice.number').notEmpty(),
  body('extractedData.invoice.date').isISO8601(),
  body('extractedData.invoice.total').isFloat({ min: 0 }),
  body('sourceFileKey').notEmpty(),
  body('iso20022FileKey').optional().isString(),
];

export const updateStatusValidation = [
  param('id').isUUID(),
  body('paymentStatus').isIn(Object.values(PaymentStatus)),
];

export const linkPoValidation = [
  param('id').isUUID(),
  body('purchaseOrderId').isUUID(),
];

// Controllers
export const list = async (req: Request, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      throw new AppError('Validation failed', 400);
    }

    const filters = {
      status: req.query.status as PaymentStatus,
      vendorId: req.query.vendor_id as string,
      hasPoMatch: req.query.has_po_match as boolean | undefined,
      hasVarianceWarning: req.query.has_variance_warning as boolean | undefined,
      startDate: req.query.start_date as string,
      endDate: req.query.end_date as string,
      page: req.query.page ? parseInt(req.query.page as string) : undefined,
      limit: req.query.limit ? parseInt(req.query.limit as string) : undefined,
      sortBy: req.query.sort_by as 'invoice_date' | 'total_amount' | 'created_at',
      sortOrder: req.query.sort_order as 'ASC' | 'DESC',
    };

    const result = await invoiceService.findAll(filters);
    res.json(result);
  } catch (error) {
    next(error);
  }
};

export const getById = async (req: Request, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      throw new AppError('Validation failed', 400);
    }

    const { id } = req.params;
    const invoice = await invoiceService.findById(id);

    res.json(invoice);
  } catch (error) {
    next(error);
  }
};

export const create = async (req: Request, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      console.error('Invoice validation errors:', JSON.stringify(errors.array(), null, 2));
      console.error('Received payload:', JSON.stringify(req.body, null, 2));
      return res.status(400).json({
        error: {
          code: 400,
          message: 'Validation failed',
          details: errors.array(),
          timestamp: new Date().toISOString(),
          path: req.path,
        },
      });
    }

    const invoice = await invoiceService.createFromExtractedData(req.body);
    res.status(201).json(invoice);
  } catch (error) {
    next(error);
  }
};

export const updateStatus = async (
  req: Request,
  res: Response,
  next: NextFunction
) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      throw new AppError('Validation failed', 400);
    }

    const { id } = req.params;
    const { paymentStatus } = req.body;

    const invoice = await invoiceService.updatePaymentStatus(id, paymentStatus);
    res.json(invoice);
  } catch (error) {
    next(error);
  }
};

export const linkPo = async (req: Request, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      throw new AppError('Validation failed', 400);
    }

    const { id } = req.params;
    const { purchaseOrderId } = req.body;

    const invoice = await invoiceService.linkToPurchaseOrder(id, purchaseOrderId);
    res.json(invoice);
  } catch (error) {
    next(error);
  }
};

export const getStats = async (req: Request, res: Response, next: NextFunction) => {
  try {
    const stats = await invoiceService.getStatistics();
    res.json(stats);
  } catch (error) {
    next(error);
  }
};

// Upload invoice file
export const uploadInvoice = async (req: Request, res: Response, next: NextFunction) => {
  try {
    if (!req.file) {
      throw new AppError('No file uploaded', 400);
    }

    const file = req.file;
    const AWS = require('aws-sdk');
    const s3 = new AWS.S3({ region: process.env.AWS_REGION || 'us-east-1' });

    const inputBucket = process.env.S3_INPUT_BUCKET;

    if (!inputBucket) {
      throw new AppError('S3_INPUT_BUCKET environment variable not configured', 500);
    }

    // Generate unique key for the file
    const timestamp = Date.now();
    const fileExtension = file.originalname.split('.').pop()?.toLowerCase();
    const key = `invoices/${timestamp}-${Math.random().toString(36).substring(7)}.${fileExtension}`;

    // Determine correct content type based on file extension
    const contentTypeMap: { [key: string]: string } = {
      'pdf': 'application/pdf',
      'png': 'image/png',
      'jpg': 'image/jpeg',
      'jpeg': 'image/jpeg',
      'gif': 'image/gif',
      'webp': 'image/webp',
    };
    const contentType = contentTypeMap[fileExtension || ''] || file.mimetype || 'application/octet-stream';

    // Create document job BEFORE uploading to S3
    const job = await documentJobService.createJob({
      jobType: JobType.INVOICE,
      sourceFileKey: key,
    });

    // Upload to S3 input bucket - this will trigger the Lambda
    // Include job ID in metadata so Lambda can update job status
    const params = {
      Bucket: inputBucket,
      Key: key,
      Body: file.buffer,
      ContentType: contentType,
      ServerSideEncryption: 'AES256',
      Metadata: {
        jobId: job.id,
      },
    };

    await s3.putObject(params).promise();

    res.status(200).json({
      message: 'Invoice uploaded successfully',
      jobId: job.id,
      fileKey: key,
      fileName: file.originalname,
      fileSize: file.size,
      status: 'processing',
    });
  } catch (error) {
    next(error);
  }
};

// Download invoice file
export const downloadInvoice = async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { id } = req.params;

    // Get invoice to find source file key
    const invoice = await invoiceService.findById(id);

    if (!invoice.sourceFileKey) {
      throw new AppError('Invoice file not found', 404);
    }

    const AWS = require('aws-sdk');
    const s3 = new AWS.S3({ region: process.env.AWS_REGION || 'us-east-1' });

    const inputBucket = process.env.S3_INPUT_BUCKET;

    if (!inputBucket) {
      throw new AppError('S3_INPUT_BUCKET environment variable not configured', 500);
    }

    // Generate signed URL (1 hour expiration)
    const params = {
      Bucket: inputBucket,
      Key: invoice.sourceFileKey,
      Expires: 3600, // 1 hour
    };

    const url = await s3.getSignedUrlPromise('getObject', params);

    res.status(200).json({
      url,
      fileName: invoice.sourceFileKey.split('/').pop(),
      expiresIn: 3600,
    });
  } catch (error) {
    next(error);
  }
};

export const downloadInvoiceValidation = [param('id').isUUID()];


export const downloadIso20022Validation = [param('id').isUUID()];

export const downloadIso20022 = async (req: Request, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      return res.status(400).json({ errors: errors.array() });
    }

    const { id } = req.params;
    const invoice = await invoiceService.findById(id);

    if (!invoice.iso20022FileKey) {
      throw new AppError('No ISO20022 file available for this invoice', 404);
    }

    // Get S3 client and generate signed URL
    const AWS = require('aws-sdk');
    const s3 = new AWS.S3({ region: process.env.AWS_REGION || 'us-east-1' });

    // Extract bucket and key from S3 URL or use directly
    const bucketName = process.env.ISO20022_BUCKET_NAME;
    
    if (!bucketName) {
      throw new AppError('ISO20022_BUCKET_NAME environment variable not configured', 500);
    }
    
    const fileKey = invoice.iso20022FileKey.replace(`s3://${bucketName}/`, '');

    const params = {
      Bucket: bucketName,
      Key: fileKey,
      Expires: 300, // URL expires in 5 minutes
      ResponseContentDisposition: `attachment; filename="${invoice.invoiceNumber}_ISO20022.xml"`,
      ResponseContentType: 'application/xml',
    };

    const signedUrl = s3.getSignedUrl('getObject', params);

    res.json({ downloadUrl: signedUrl });
  } catch (error) {
    next(error);
  }
};
