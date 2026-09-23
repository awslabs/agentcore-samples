import { DataSource } from 'typeorm';
import { PurchaseOrderRepository } from '../repositories/PurchaseOrderRepository';
import { PurchaseOrder } from '../entities/PurchaseOrder';

export class PurchaseOrderService {
  private repository: PurchaseOrderRepository;

  constructor(dataSource: DataSource) {
    this.repository = new PurchaseOrderRepository(dataSource);
  }

  async getAllPurchaseOrders(): Promise<any[]> {
    try {
      const pos = await this.repository.findAll();
      console.log(`[PurchaseOrderService] Found ${pos.length} purchase orders`);
      
      if (pos.length === 0) {
        console.warn('[PurchaseOrderService] No purchase orders found in database. Database may need seeding.');
      }
      
      return pos.map(po => this.transformPOForFrontend(po));
    } catch (error) {
      console.error('[PurchaseOrderService.getAllPurchaseOrders] Error:', error);
      // Return empty array to prevent 500 errors
      return [];
    }
  }

  async getPurchaseOrderById(id: string): Promise<any | null> {
    const po = await this.repository.findById(id);
    
    if (!po) {
      return null;
    }
    
    return this.transformPOForFrontend(po);
  }

  async createPurchaseOrder(poData: any): Promise<any> {
    const po = await this.repository.create(poData);
    return this.transformPOForFrontend(po);
  }

  private transformPOForFrontend(po: PurchaseOrder): any {
    const orderedQuantity = po.items?.reduce((sum, item) => sum + Number(item.quantity), 0) || 0;
    const receivedQuantity = po.items?.reduce((sum, item) => sum + Number(item.receivedQuantity), 0) || 0;
    const fulfillmentPercentage = orderedQuantity > 0 ? (receivedQuantity / orderedQuantity) * 100 : 0;
    const remainingAmount = Number(po.totalAmount) - (Number(po.totalAmount) * fulfillmentPercentage / 100);
    
    const hasReceipt = receivedQuantity > 0;
    const hasPartialReceipt = receivedQuantity > 0 && receivedQuantity < orderedQuantity;
    
    const isOverdue = po.dueDate ? new Date(po.dueDate) < new Date() : false;

    return {
      id: po.id,
      poNumber: po.poNumber,
      vendor: {
        id: po.vendor.id,
        name: po.vendor.name,
        code: po.vendor.code,
      },
      sourceLocation: po.sourceLocation,
      assignedDate: po.assignedDate,
      dueDate: po.dueDate,
      status: po.status,
      totalAmount: Number(po.totalAmount),
      remainingAmount,
      currency: po.currency || 'USD',
      warehouse: po.warehouse,
      orderedQuantity,
      receivedQuantity,
      fulfillmentPercentage: Math.round(fulfillmentPercentage),
      hasReceipt,
      hasPartialReceipt,
      hasInvoice: false, // TODO: Calculate from invoices
      matchStatus: hasReceipt ? '3-way' : 'none',
      variancePercentage: 0, // TODO: Calculate from invoices
      attachments: {
        poDocuments: 1,
        receipts: 0, // TODO: Count from receipts
        invoices: 0, // TODO: Count from invoices
        total: 1,
      },
      isOverdue,
      items: po.items?.map(item => ({
        id: item.id,
        description: item.description,
        quantity: Number(item.quantity),
        receivedQuantity: Number(item.receivedQuantity),
        unitPrice: Number(item.unitPrice),
        totalPrice: Number(item.totalPrice),
      })) || [],
    };
  }
}
