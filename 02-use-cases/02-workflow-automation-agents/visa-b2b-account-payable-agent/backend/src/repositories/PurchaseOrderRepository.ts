import { DataSource, Repository } from 'typeorm';
import { PurchaseOrder } from '../entities/PurchaseOrder';

export class PurchaseOrderRepository {
  private repository: Repository<PurchaseOrder>;

  constructor(dataSource: DataSource) {
    this.repository = dataSource.getRepository(PurchaseOrder);
  }

  async findAll(): Promise<PurchaseOrder[]> {
    try {
      return await this.repository.find({
        relations: ['vendor', 'items'],
        order: { assignedDate: 'DESC' },
      });
    } catch (error) {
      console.error('[PurchaseOrderRepository.findAll] Error:', error);
      // Return empty array instead of throwing to prevent 500 errors
      return [];
    }
  }

  async findById(id: string): Promise<PurchaseOrder | null> {
    return this.repository.findOne({
      where: { id },
      relations: ['vendor', 'items'],
    });
  }

  async findByPoNumber(poNumber: string): Promise<PurchaseOrder | null> {
    return this.repository.findOne({
      where: { poNumber },
      relations: ['vendor', 'items'],
    });
  }

  async create(poData: any): Promise<PurchaseOrder> {
    // Extract items from poData
    const { items, ...poFields } = poData;
    
    // Generate PO number if not provided
    if (!poFields.poNumber) {
      const year = new Date().getFullYear();
      const count = await this.repository.count();
      poFields.poNumber = `PO-${year}-${String(count + 1).padStart(5, '0')}`;
    }
    
    // Set assigned date to today if not provided
    if (!poFields.assignedDate) {
      poFields.assignedDate = new Date().toISOString().split('T')[0];
    }
    
    // Set default status if not provided
    if (!poFields.status) {
      poFields.status = 'active';
    }
    
    // Set default currency if not provided
    if (!poFields.currency) {
      poFields.currency = 'USD';
    }
    
    // Process items: calculate total_price for each item and overall total
    const processedItems = (items || []).map((item: any) => ({
      ...item,
      totalPrice: item.totalPrice || (item.quantity || 0) * (item.unitPrice || 0),
      receivedQuantity: item.receivedQuantity || 0,
    }));
    
    // Calculate total amount from items if not provided
    if (!poFields.totalAmount && processedItems.length > 0) {
      poFields.totalAmount = processedItems.reduce((sum: number, item: any) => {
        return sum + (item.totalPrice || 0);
      }, 0);
    }
    
    // Create the PO entity with items
    const po = this.repository.create({
      ...poFields,
      items: processedItems,
    });
    
    // Save with cascade to create items
    const saved = await this.repository.save(po);
    const savedPO = Array.isArray(saved) ? saved[0] : saved;
    
    // Reload with relations to get vendor and items
    return this.findById(savedPO.id) as Promise<PurchaseOrder>;
  }

  async update(id: string, poData: Partial<PurchaseOrder>): Promise<PurchaseOrder | null> {
    await this.repository.update(id, poData);
    return this.findById(id);
  }

  async delete(id: string): Promise<boolean> {
    const result = await this.repository.delete(id);
    return (result.affected ?? 0) > 0;
  }
}
