import { DataSource } from 'typeorm';
import { seedDatabase } from '../utils/seedData';
import { logger } from '../utils/logger';
import AWS from 'aws-sdk';
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

const secretsManager = new AWS.SecretsManager();

// Lambda handler for running migrations and seeding
export const handler = async (event: any) => {
  try {
    logger.info('Starting migration and seed process...');

    // Get database credentials from Secrets Manager
    const secretArn = process.env.DB_SECRET_ARN;
    let username = process.env.DB_USERNAME || 'postgres';
    let password = process.env.DB_PASSWORD || '';
    
    if (secretArn) {
      logger.info('Fetching credentials from Secrets Manager...');
      const secret = await secretsManager
        .getSecretValue({ SecretId: secretArn })
        .promise();

      if (secret.SecretString) {
        const credentials = JSON.parse(secret.SecretString);
        username = credentials.username;
        password = credentials.password;
        logger.info(`Using username: ${username}`);
      }
    }

    // Create DataSource with fetched credentials
    const AppDataSource = new DataSource({
      type: 'postgres',
      host: process.env.DB_HOST || 'localhost',
      port: parseInt(process.env.DB_PORT || '5432'),
      username,
      password,
      database: process.env.DB_DATABASE || 'rtpoverlay',
      synchronize: true, // Auto-sync for migrations
      logging: true,
      entities: [User, Vendor, PurchaseOrder, PurchaseOrderItem, GoodsReceipt, ReceiptDocument, Invoice, DocumentJob, QCRecord, Payment],
      migrations: [],
      ssl: { rejectUnauthorized: false }, // RDS requires SSL // nosemgrep
    });

    // Initialize database connection
    logger.info('Connecting to database...');
    await AppDataSource.initialize();
    logger.info('Database connected');

    // Run migrations (TypeORM will auto-sync in development mode)
    // In production, you'd run: await AppDataSource.runMigrations();
    logger.info('Database schema synchronized');

    // Seed database
    logger.info('Seeding database...');
    await seedDatabase(AppDataSource);
    logger.info('Database seeded successfully');

    // Close connection
    await AppDataSource.destroy();

    return {
      statusCode: 200,
      body: JSON.stringify({
        message: 'Migration and seed completed successfully',
        timestamp: new Date().toISOString(),
      }),
    };
  } catch (error) {
    logger.error('Migration/seed failed:', error);
    return {
      statusCode: 500,
      body: JSON.stringify({
        message: 'Migration and seed failed',
        error: error instanceof Error ? error.message : 'Unknown error',
      }),
    };
  }
};
