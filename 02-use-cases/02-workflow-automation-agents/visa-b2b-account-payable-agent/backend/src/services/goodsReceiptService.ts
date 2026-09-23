import { getDataSource } from '../config/database';
import { GoodsReceipt, GoodsReceiptStatus } from '../entities/GoodsReceipt';
import { PurchaseOrder, PurchaseOrderStatus } from '../entities/PurchaseOrder';
import { AppError } from '../middleware/errorHandler';

export interface CreateGoodsReceiptDto {
  purchaseOrderId: string;
  deliveryDate: Date;
  quantityReceived: number;
  conditionNotes?: string;
  deliveryReference: string;
  receivedById: string;
}

export class GoodsReceiptService {
  private async getGrRepository() {
    const dataSource = await getDataSource();
    return dataSource.getRepository(GoodsReceipt);
  }
  
  private async getPoRepository() {
    const dataSource = await getDataSource();
    return dataSource.getRepository(PurchaseOrder);
  }

  async create(dto: CreateGoodsReceiptDto) {
    const poRepository = await this.getPoRepository();
    const grRepository = await this.getGrRepository();
    
    // Verify purchase order exists
    const po = await poRepository.findOne({
      where: { id: dto.purchaseOrderId },
    });

    if (!po) {
      throw new AppError('Purchase order not found', 404);
    }

    if (po.status === PurchaseOrderStatus.COMPLETED) {
      throw new AppError('Purchase order is already completed', 400);
    }

    // Create goods receipt
    const goodsReceipt = grRepository.create({
      purchaseOrderId: dto.purchaseOrderId,
      deliveryDate: dto.deliveryDate,
      quantityReceived: dto.quantityReceived,
      conditionNotes: dto.conditionNotes,
      deliveryReference: dto.deliveryReference,
      receivedById: dto.receivedById,
      status: GoodsReceiptStatus.PENDING_QC,
    });

    const saved = await grRepository.save(goodsReceipt);

    // Update purchase order items with received quantity
    const dataSource = await getDataSource();
    const { PurchaseOrderItem } = await import('../entities/PurchaseOrderItem');
    const poItemRepository = dataSource.getRepository(PurchaseOrderItem);
    
    // Get PO with items
    const poWithItems = await poRepository.findOne({
      where: { id: dto.purchaseOrderId },
      relations: ['items'],
    });

    if (poWithItems && poWithItems.items && poWithItems.items.length > 0) {
      // Update the first item's received quantity (or distribute across items)
      const item = poWithItems.items[0];
      const currentReceived = Number(item.receivedQuantity) || 0;
      const newReceivedQty = currentReceived + dto.quantityReceived;
      
      console.log(`[GoodsReceiptService] Updating PO item ${item.id}: ${currentReceived} + ${dto.quantityReceived} = ${newReceivedQty}`);
      
      // Use query builder to force update
      await poItemRepository
        .createQueryBuilder()
        .update()
        .set({ receivedQuantity: newReceivedQty })
        .where('id = :id', { id: item.id })
        .execute();
      
      console.log(`[GoodsReceiptService] Successfully updated PO item ${item.id}: receivedQuantity = ${newReceivedQty}`);
    }

    // Update purchase order status
    po.status = PurchaseOrderStatus.RECEIVED;
    await poRepository.save(po);

    // Return with relations
    return grRepository.findOne({
      where: { id: saved.id },
      relations: ['purchaseOrder', 'purchaseOrder.vendor', 'receivedBy', 'documents'],
    });
  }

  async findAll(filters: {
    status?: GoodsReceiptStatus;
    page?: number;
    limit?: number;
  }) {
    const grRepository = await this.getGrRepository();
    const page = filters.page || 1;
    const limit = Math.min(filters.limit || 10, 100);
    const skip = (page - 1) * limit;

    const queryBuilder = grRepository
      .createQueryBuilder('gr')
      .leftJoinAndSelect('gr.purchaseOrder', 'po')
      .leftJoinAndSelect('po.vendor', 'vendor')
      .leftJoinAndSelect('gr.receivedBy', 'user')
      .leftJoinAndSelect('gr.documents', 'documents')
      .orderBy('gr.deliveryDate', 'DESC');

    if (filters.status) {
      queryBuilder.andWhere('gr.status = :status', { status: filters.status });
    }

    const [data, total] = await queryBuilder.skip(skip).take(limit).getManyAndCount();

    return {
      data,
      pagination: {
        page,
        limit,
        total,
        totalPages: Math.ceil(total / limit),
      },
    };
  }

  async findById(id: string) {
    const grRepository = await this.getGrRepository();
    const gr = await grRepository.findOne({
      where: { id },
      relations: ['purchaseOrder', 'purchaseOrder.vendor', 'purchaseOrder.items', 'receivedBy', 'documents'],
    });

    if (!gr) {
      throw new AppError('Goods receipt not found', 404);
    }

    return gr;
  }

  async createFromExtraction(data: {
    bolNumber: string;
    poReference?: string;
    vendorName?: string;
    deliveryDate: Date;
    materialDescription: string;
    quantityReceived: number;
    unit: string;
    receivedBy: string;
    deliveryLocation: string;
    notes: string;
    confidenceScores: any;
    sourceFileKey: string;
    extractedData: any;
    needsReview: boolean;
    missingFields: string[];
    lowConfidenceFields: string[];
  }) {
    const grRepository = await this.getGrRepository();
    const poRepository = await this.getPoRepository();
    
    // Check for existing GR with same BOL number (allow re-upload)
    const existingGr = await grRepository.findOne({
      where: { deliveryReference: data.bolNumber },
      relations: ['purchaseOrder'],
    });
    
    // Try to find PO by reference if provided
    let purchaseOrder = null;
    if (data.poReference) {
      purchaseOrder = await poRepository.findOne({
        where: { poNumber: data.poReference },
      });
    }

    // Get a system user for automated receipts (use first user as fallback)
    const dataSource = await this.getPoRepository().then(r => r.manager);
    const { User } = await import('../entities/User');
    const userRepo = dataSource.getRepository(User);
    const systemUser = await userRepo.findOne({ where: {} }); // Get any user as system user
    
    if (!systemUser) {
      throw new AppError('No users found in system. Cannot create automated GR.', 500);
    }

    let saved: GoodsReceipt;

    if (existingGr) {
      console.log(`GR with BOL ${data.bolNumber} already exists - updating with new data`);
      
      // Update existing GR
      existingGr.purchaseOrderId = purchaseOrder?.id || existingGr.purchaseOrderId;
      existingGr.deliveryDate = data.deliveryDate;
      existingGr.quantityReceived = data.quantityReceived;
      existingGr.conditionNotes = data.notes || `Extracted from BOL: ${data.bolNumber}. ${data.needsReview ? 'NEEDS REVIEW: ' + data.missingFields.join(', ') : ''}`;
      existingGr.status = data.needsReview ? GoodsReceiptStatus.PENDING_QC : GoodsReceiptStatus.QC_PASSED;
      
      saved = await grRepository.save(existingGr);
    } else {
      // Create new goods receipt
      const goodsReceipt = grRepository.create({
        purchaseOrderId: purchaseOrder?.id,
        deliveryDate: data.deliveryDate,
        quantityReceived: data.quantityReceived,
        conditionNotes: data.notes || `Extracted from BOL: ${data.bolNumber}. ${data.needsReview ? 'NEEDS REVIEW: ' + data.missingFields.join(', ') : ''}`,
        deliveryReference: data.bolNumber,
        receivedById: systemUser.id,
        status: data.needsReview ? GoodsReceiptStatus.PENDING_QC : GoodsReceiptStatus.QC_PASSED,
      });

      saved = await grRepository.save(goodsReceipt);
    }

    // Update PO status and items if linked
    if (purchaseOrder) {
      purchaseOrder.status = PurchaseOrderStatus.RECEIVED;
      await poRepository.save(purchaseOrder);
      
      // Update PO items with received quantity
      const dataSource = await getDataSource();
      const { PurchaseOrderItem } = await import('../entities/PurchaseOrderItem');
      const poItemRepository = dataSource.getRepository(PurchaseOrderItem);
      
      // Get PO with items
      const poWithItems = await poRepository.findOne({
        where: { id: purchaseOrder.id },
        relations: ['items'],
      });

      if (poWithItems && poWithItems.items && poWithItems.items.length > 0) {
        // Update the first item's received quantity
        const item = poWithItems.items[0];
        const currentReceived = Number(item.receivedQuantity) || 0;
        const newReceivedQty = currentReceived + data.quantityReceived;
        
        console.log(`[GoodsReceiptService.createFromExtraction] Updating PO item ${item.id}: ${currentReceived} + ${data.quantityReceived} = ${newReceivedQty}`);
        
        // Use query builder to force update
        await poItemRepository
          .createQueryBuilder()
          .update()
          .set({ receivedQuantity: newReceivedQty })
          .where('id = :id', { id: item.id })
          .execute();
        
        console.log(`[GoodsReceiptService.createFromExtraction] Successfully updated PO item ${item.id}: receivedQuantity = ${newReceivedQty}`);
      }
    }

    // Return with relations
    return grRepository.findOne({
      where: { id: saved.id },
      relations: ['purchaseOrder', 'purchaseOrder.vendor', 'receivedBy', 'documents'],
    });
  }
}
