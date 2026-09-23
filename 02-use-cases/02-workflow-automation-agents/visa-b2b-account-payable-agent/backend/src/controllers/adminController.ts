import { Request, Response, NextFunction } from 'express';
import { getDataSource } from '../config/database';
import { AppError } from '../middleware/errorHandler';

/**
 * Clear all data from the database (keep schema)
 * DELETE /api/admin/clear-data
 */
export const clearData = async (req: Request, res: Response, next: NextFunction) => {
  try {
    const dataSource = await getDataSource();
    const queryRunner = dataSource.createQueryRunner();
    await queryRunner.connect();

    try {
      // Delete in order to respect foreign key constraints
      await queryRunner.query('DELETE FROM document_jobs');
      await queryRunner.query('DELETE FROM invoices');
      await queryRunner.query('DELETE FROM goods_receipts');
      await queryRunner.query('DELETE FROM qc_records');
      await queryRunner.query('DELETE FROM purchase_orders');
      await queryRunner.query('DELETE FROM purchase_order_items');
      await queryRunner.query('DELETE FROM vendors');
      
      // Keep users table so you can still login

      res.json({
        message: 'All data cleared successfully',
        cleared: [
          'document_jobs',
          'invoices',
          'goods_receipts',
          'qc_records',
          'purchase_orders',
          'purchase_order_items',
          'vendors'
        ]
      });
    } finally {
      await queryRunner.release();
    }
  } catch (error) {
    next(error);
  }
};

/**
 * Clear only invoices and goods receipts (keep POs, vendors, users)
 * DELETE /api/admin/clear-invoices-gr
 */
export const clearInvoicesAndGR = async (req: Request, res: Response, next: NextFunction) => {
  try {
    const dataSource = await getDataSource();
    const queryRunner = dataSource.createQueryRunner();
    await queryRunner.connect();

    try {
      // Delete in order to respect foreign key constraints
      
      // 1. Delete child tables first
      await queryRunner.query('DELETE FROM receipt_documents');
      await queryRunner.query('DELETE FROM payments');
      await queryRunner.query('DELETE FROM qc_records');
      await queryRunner.query('DELETE FROM document_jobs');
      
      // 2. Delete parent tables
      await queryRunner.query('DELETE FROM invoices');
      await queryRunner.query('DELETE FROM goods_receipts');

      // 3. Reset PO status and received quantities
      await queryRunner.query("UPDATE purchase_orders SET status = 'active'");
      await queryRunner.query('UPDATE purchase_order_items SET received_quantity = 0');

      res.json({
        message: 'Invoices and Goods Receipts cleared successfully',
        cleared: [
          'receipt_documents',
          'payments',
          'qc_records',
          'document_jobs',
          'invoices',
          'goods_receipts'
        ],
        reset: [
          'purchase_orders.status → active',
          'purchase_order_items.received_quantity → 0'
        ],
        kept: [
          'purchase_orders',
          'purchase_order_items',
          'vendors',
          'users'
        ]
      });
    } finally {
      await queryRunner.release();
    }
  } catch (error) {
    next(error);
  }
};
