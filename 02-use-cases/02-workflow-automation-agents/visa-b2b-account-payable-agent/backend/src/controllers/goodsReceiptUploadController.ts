import { Request, Response, NextFunction } from 'express';
import { DocumentJobService } from '../services/documentJobService';
import { JobType } from '../entities/DocumentJob';
import { AppError } from '../middleware/errorHandler';

const documentJobService = new DocumentJobService();

export const uploadGoodsReceipt = async (req: Request, res: Response, next: NextFunction) => {
  try {
    if (!req.file) {
      throw new AppError('No file uploaded', 400);
    }

    const file = req.file;
    const AWS = require('aws-sdk');
    const s3 = new AWS.S3({ region: process.env.AWS_REGION || 'us-east-1' });

    const inputBucket = process.env.S3_GR_INPUT_BUCKET || process.env.S3_INPUT_BUCKET;

    if (!inputBucket) {
      throw new AppError('S3_GR_INPUT_BUCKET environment variable not configured', 500);
    }

    // Generate unique key for the file
    const timestamp = Date.now();
    const fileExtension = file.originalname.split('.').pop()?.toLowerCase();
    const key = `receipts/${timestamp}-${Math.random().toString(36).substring(7)}.${fileExtension}`;

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
      jobType: JobType.GOODS_RECEIPT,
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
      message: 'Goods receipt uploaded successfully',
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
