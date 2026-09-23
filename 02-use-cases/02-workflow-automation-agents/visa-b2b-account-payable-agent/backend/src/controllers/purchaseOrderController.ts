import { Request, Response } from 'express';
import { getDataSource } from '../config/database';
import { PurchaseOrderService } from '../services/purchaseOrderService';

export const getPurchaseOrders = async (req: Request, res: Response): Promise<void> => {
  try {
    console.log('[getPurchaseOrders] Starting request...');
    const dataSource = await getDataSource();
    console.log('[getPurchaseOrders] DataSource obtained, isInitialized:', dataSource.isInitialized);
    
    const purchaseOrderService = new PurchaseOrderService(dataSource);
    console.log('[getPurchaseOrders] Service created, fetching POs...');
    
    const pos = await purchaseOrderService.getAllPurchaseOrders();
    console.log('[getPurchaseOrders] Found', pos.length, 'purchase orders');
    
    res.json(pos);
  } catch (error) {
    console.error('[getPurchaseOrders] Error:', error);
    console.error('[getPurchaseOrders] Error stack:', error instanceof Error ? error.stack : 'No stack trace');
    res.status(500).json({ 
      error: 'Failed to fetch purchase orders',
      message: error instanceof Error ? error.message : 'Unknown error',
      details: process.env.NODE_ENV === 'development' ? error : undefined
    });
  }
};

export const getPurchaseOrderById = async (req: Request, res: Response): Promise<void> => {
  try {
    const { id } = req.params;
    const dataSource = await getDataSource();
    const purchaseOrderService = new PurchaseOrderService(dataSource);
    const po = await purchaseOrderService.getPurchaseOrderById(id);
    
    if (!po) {
      res.status(404).json({ error: 'Purchase order not found' });
      return;
    }
    
    res.json(po);
  } catch (error) {
    console.error('Error fetching purchase order:', error);
    res.status(500).json({ error: 'Failed to fetch purchase order' });
  }
};

export const createPurchaseOrder = async (req: Request, res: Response): Promise<void> => {
  try {
    console.log('[createPurchaseOrder] Request body:', JSON.stringify(req.body, null, 2));
    const dataSource = await getDataSource();
    const purchaseOrderService = new PurchaseOrderService(dataSource);
    const po = await purchaseOrderService.createPurchaseOrder(req.body);
    console.log('[createPurchaseOrder] Successfully created PO:', po.id);
    res.status(201).json(po);
  } catch (error) {
    console.error('[createPurchaseOrder] Error:', error);
    console.error('[createPurchaseOrder] Error details:', error instanceof Error ? error.message : 'Unknown error');
    console.error('[createPurchaseOrder] Stack:', error instanceof Error ? error.stack : 'No stack');
    res.status(500).json({ 
      error: 'Failed to create purchase order',
      message: error instanceof Error ? error.message : 'Unknown error',
      details: process.env.NODE_ENV === 'development' ? error : undefined
    });
  }
};

export const getDatabaseStatus = async (req: Request, res: Response): Promise<void> => {
  try {
    const dataSource = await getDataSource();
    
    // Check if database is connected
    const isConnected = dataSource.isInitialized;
    
    // Count records in key tables
    const poCount = await dataSource.getRepository('PurchaseOrder').count();
    const vendorCount = await dataSource.getRepository('Vendor').count();
    const userCount = await dataSource.getRepository('User').count();
    
    res.json({
      status: 'ok',
      database: {
        connected: isConnected,
        type: dataSource.options.type,
      },
      counts: {
        purchaseOrders: poCount,
        vendors: vendorCount,
        users: userCount,
      },
      needsSeeding: poCount === 0 || vendorCount === 0 || userCount === 0,
    });
  } catch (error) {
    console.error('Error checking database status:', error);
    res.status(500).json({ 
      status: 'error',
      error: error instanceof Error ? error.message : 'Unknown error',
    });
  }
};
