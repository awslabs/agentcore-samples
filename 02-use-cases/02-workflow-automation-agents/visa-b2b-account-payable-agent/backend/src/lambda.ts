import serverless from 'serverless-http';
import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import rateLimit from 'express-rate-limit';
import dotenv from 'dotenv';
import { getDataSource } from './config/database';
import { logger } from './utils/logger';
import { errorHandler } from './middleware/errorHandler';
import authRoutes from './routes/authRoutes';
import purchaseOrderRoutes from './routes/purchaseOrderRoutes';
import goodsReceiptRoutes from './routes/goodsReceiptRoutes';
import vendorRoutes from './routes/vendorRoutes';
import invoiceRoutes from './routes/invoiceRoutes';
import paymentRoutes from './routes/paymentRoutes';
import supplierRoutes from './routes/supplierRoutes';
import aiInsightsRoutes from './routes/aiInsightsRoutes';
import documentJobRoutes from './routes/documentJobRoutes';
import adminRoutes from './routes/adminRoutes';

// Load environment variables
dotenv.config();

const app = express();

// Rate limiting
const apiLimiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 100,
  standardHeaders: true,
  legacyHeaders: false,
});

// Middleware
app.use(helmet());
app.use(
  cors({
    origin: process.env.CORS_ORIGIN || 'http://localhost:5173',
    credentials: true,
  })
);
app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use('/api/', apiLimiter);

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

// API routes
app.use('/api/auth', authRoutes);
app.use('/api/purchase-orders', purchaseOrderRoutes);
app.use('/api/goods-receipts', goodsReceiptRoutes);
app.use('/api/vendors', vendorRoutes);
app.use('/api/invoices', invoiceRoutes);
app.use('/api/payments', paymentRoutes);
app.use('/api/supplier', supplierRoutes);
app.use('/api/ai', aiInsightsRoutes);
app.use('/api/document-jobs', documentJobRoutes);
app.use('/api/admin', adminRoutes);

// Error handling middleware (must be last)
app.use(errorHandler);

// Initialize database connection (Lambda will reuse this)
let isDbInitialized = false;

const initializeDatabase = async () => {
  if (!isDbInitialized) {
    try {
      await getDataSource();
      logger.info('Database connection established');
      isDbInitialized = true;
    } catch (error) {
      logger.error('Failed to initialize database:', error);
      throw error;
    }
  }
};

// Lambda handler
export const handler = async (event: any, context: any) => {
  // Initialize database on cold start
  await initializeDatabase();

  // Create serverless handler with binary support
  const serverlessHandler = serverless(app, {
    binary: ['multipart/form-data', 'image/*', 'application/pdf'],
  });
  return serverlessHandler(event, context);
};
