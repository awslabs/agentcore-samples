import AWS from 'aws-sdk';
import { AppError } from '../middleware/errorHandler';
import { logger } from '../utils/logger';

const s3 = new AWS.S3({
  region: process.env.AWS_REGION || 'us-east-1',
});

const BUCKET_NAME = process.env.AWS_S3_BUCKET || '';

export class S3Service {
  async uploadFile(file: Express.Multer.File, key: string): Promise<string> {
    if (!BUCKET_NAME) {
      throw new AppError('S3 bucket not configured', 500);
    }

    try {
      const params: AWS.S3.PutObjectRequest = {
        Bucket: BUCKET_NAME,
        Key: key,
        Body: file.buffer,
        ContentType: file.mimetype,
        ServerSideEncryption: 'AES256',
      };

      await s3.putObject(params).promise();

      logger.info(`File uploaded to S3: ${key}`);

      return key;
    } catch (error) {
      logger.error('S3 upload error:', error);
      throw new AppError('Failed to upload file', 500);
    }
  }

  async getSignedUrl(key: string, expiresIn: number = 3600): Promise<string> {
    if (!BUCKET_NAME) {
      throw new AppError('S3 bucket not configured', 500);
    }

    try {
      const params = {
        Bucket: BUCKET_NAME,
        Key: key,
        Expires: expiresIn,
      };

      const url = await s3.getSignedUrlPromise('getObject', params);
      return url;
    } catch (error) {
      logger.error('S3 signed URL error:', error);
      throw new AppError('Failed to generate signed URL', 500);
    }
  }

  async deleteFile(key: string): Promise<void> {
    if (!BUCKET_NAME) {
      throw new AppError('S3 bucket not configured', 500);
    }

    try {
      const params = {
        Bucket: BUCKET_NAME,
        Key: key,
      };

      await s3.deleteObject(params).promise();
      logger.info(`File deleted from S3: ${key}`);
    } catch (error) {
      logger.error('S3 delete error:', error);
      throw new AppError('Failed to delete file', 500);
    }
  }
}
