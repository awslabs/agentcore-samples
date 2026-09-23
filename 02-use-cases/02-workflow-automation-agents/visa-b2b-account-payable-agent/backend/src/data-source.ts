import { DataSource } from 'typeorm';
import dotenv from 'dotenv';
import { User } from './entities/User';
import { PurchaseOrder } from './entities/PurchaseOrder';
import { PurchaseOrderItem } from './entities/PurchaseOrderItem';
import { GoodsReceipt } from './entities/GoodsReceipt';
import { Vendor } from './entities/Vendor';
import { Invoice } from './entities/Invoice';
import { DocumentJob } from './entities/DocumentJob';
import { QCRecord } from './entities/QCRecord';
import { ReceiptDocument } from './entities/ReceiptDocument';
import { Payment } from './entities/Payment';

dotenv.config();

export const AppDataSource = new DataSource({
  type: 'postgres',
  host: process.env.DB_HOST || 'localhost',
  port: parseInt(process.env.DB_PORT || '5432'),
  username: process.env.DB_USERNAME || 'postgres',
  password: process.env.DB_PASSWORD || '',
  database: process.env.DB_DATABASE || 'rtp_overlay',
  synchronize: false, // Never use synchronize in production
  logging: process.env.NODE_ENV === 'development',
  entities: [
    User,
    PurchaseOrder,
    PurchaseOrderItem,
    GoodsReceipt,
    Vendor,
    Invoice,
    DocumentJob,
    QCRecord,
    ReceiptDocument,
    Payment,
  ],
  migrations: ['src/migrations/**/*.ts'],
  subscribers: [],
  ssl: process.env.DB_SSL === 'true' ? { rejectUnauthorized: false } : false, // nosemgrep
});
