import { DataSource } from 'typeorm';
import dotenv from 'dotenv';
import AWS from 'aws-sdk';

dotenv.config();

// Import entities for Lambda
import { User } from '../entities/User';
import { Vendor } from '../entities/Vendor';
import { PurchaseOrder } from '../entities/PurchaseOrder';
import { PurchaseOrderItem } from '../entities/PurchaseOrderItem';
import { GoodsReceipt } from '../entities/GoodsReceipt';
import { ReceiptDocument } from '../entities/ReceiptDocument';
import { Invoice } from '../entities/Invoice';
import { DocumentJob } from '../entities/DocumentJob';
import { QCRecord } from '../entities/QCRecord';
import { Payment } from '../entities/Payment';

let dataSource: DataSource | null = null;

/**
 * Get or create the DataSource instance with lazy initialization.
 * Fetches credentials from Secrets Manager if needed.
 */
export const getDataSource = async (): Promise<DataSource> => {
  if (dataSource && dataSource.isInitialized) {
    return dataSource;
  }

  // Fetch credentials from Secrets Manager if in production
  let username = process.env.DB_USERNAME || 'postgres';
  let password = process.env.DB_PASSWORD || '';

  const secretArn = process.env.DB_SECRET_ARN;
  console.log('[getDataSource] DB_SECRET_ARN:', secretArn);
  console.log('[getDataSource] DB_USERNAME:', process.env.DB_USERNAME);
  
  if (secretArn && !process.env.DB_USERNAME) {
    console.log('[getDataSource] Fetching credentials from Secrets Manager...');
    const secretsManager = new AWS.SecretsManager();
    const secret = await secretsManager
      .getSecretValue({ SecretId: secretArn })
      .promise();

    if (secret.SecretString) {
      const credentials = JSON.parse(secret.SecretString);
      username = credentials.username;
      password = credentials.password;
      console.log('[getDataSource] Credentials fetched, username:', username);
    }
  } else {
    console.log('[getDataSource] Using environment variables for credentials');
  }

  // Create DataSource if it doesn't exist
  if (!dataSource) {
    dataSource = new DataSource({
      type: 'postgres',
      host: process.env.DB_HOST || 'localhost',
      port: parseInt(process.env.DB_PORT || '5432'),
      username,
      password,
      database: process.env.DB_DATABASE || 'rtp_overlay',
      synchronize: process.env.NODE_ENV === 'development',
      logging: process.env.NODE_ENV === 'development',
      entities: process.env.NODE_ENV === 'production' 
        ? [User, Vendor, PurchaseOrder, PurchaseOrderItem, GoodsReceipt, ReceiptDocument, Invoice, DocumentJob, QCRecord, Payment]
        : ['src/entities/**/*.ts'],
      migrations: process.env.NODE_ENV === 'production' ? [] : ['src/migrations/**/*.ts'],
      subscribers: [],
      ssl: process.env.DB_SSL === 'true' ? { rejectUnauthorized: false } : false, // nosemgrep
    });
  }

  // Initialize if not already initialized
  if (!dataSource.isInitialized) {
    await dataSource.initialize();
  }

  return dataSource;
};
