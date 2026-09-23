import { DataSource } from 'typeorm';
import { User, UserRole } from '../entities/User';
import { Vendor } from '../entities/Vendor';
import { PurchaseOrder, PurchaseOrderStatus } from '../entities/PurchaseOrder';
import { PurchaseOrderItem } from '../entities/PurchaseOrderItem';
import { GoodsReceipt, GoodsReceiptStatus } from '../entities/GoodsReceipt';
import bcrypt from 'bcrypt';
import { logger } from './logger';

export const seedDatabase = async (dataSource?: DataSource) => {
  try {
    logger.info('Starting database seeding...');

    // Use provided dataSource or get lazy-initialized one
    const ds = dataSource || (await (await import('../config/database')).getDataSource());

    const userRepo = ds.getRepository(User);
    const vendorRepo = ds.getRepository(Vendor);
    const poRepo = ds.getRepository(PurchaseOrder);
    const poItemRepo = ds.getRepository(PurchaseOrderItem);
    const grRepo = ds.getRepository(GoodsReceipt);

    // Clear any corrupted data first
    logger.info('Clearing any existing corrupted data...');
    await ds.query('DELETE FROM purchase_order_items WHERE quantity IS NULL');
    await ds.query('DELETE FROM purchase_order_items WHERE received_quantity IS NULL');

    // Check existing data
    const existingUsers = await userRepo.count();
    const existingVendors = await vendorRepo.count();
    const existingPOs = await poRepo.count();
    const existingGRs = await grRepo.count();

    logger.info(`Existing data - Users: ${existingUsers}, Vendors: ${existingVendors}, POs: ${existingPOs}, GRs: ${existingGRs}`);

    let users: User[] = [];
    let vendors: Vendor[] = [];
    let purchaseOrders: PurchaseOrder[] = [];

    // Create users only if they don't exist
    if (existingUsers === 0) {
      const hashedPassword = await bcrypt.hash('password123', 10);
      
      users = await userRepo.save([
        {
          username: 'admin',
          email: 'admin@example.com',
          passwordHash: hashedPassword,
          name: 'Admin User',
          role: UserRole.ADMIN,
          isActive: true,
        },
        {
          username: 'receiving1',
          email: 'receiving1@example.com',
          passwordHash: hashedPassword,
          name: 'John Receiver',
          role: UserRole.RECEIVING,
          isActive: true,
        },
        {
          username: 'qc1',
          email: 'qc1@example.com',
          passwordHash: hashedPassword,
          name: 'Jane Quality',
          role: UserRole.QC,
          isActive: true,
        },
        {
          username: 'treasury1',
          email: 'treasury1@example.com',
          passwordHash: hashedPassword,
          name: 'Bob Treasury',
          role: UserRole.TREASURY,
          isActive: true,
        },
      ]);

      logger.info(`Created ${users.length} users`);
    } else {
      users = await userRepo.find();
      logger.info('Users already exist, skipping user creation');
    }

    // Create vendors only if they don't exist
    if (existingVendors === 0) {
      vendors = await vendorRepo.save([
        {
          code: 'ACME001',
          name: 'Acme Corporation',
          email: 'contact@acme.com',
          phone: '+1-555-0100',
          address: '123 Industrial Way, Manufacturing City, MC 12345',
          performanceScore: 92.5,
          onTimeDeliveryRate: 95.0,
          qualityScore: 90.0,
          invoiceAccuracy: 98.0,
          isActive: true,
        },
        {
          code: 'TECH002',
          name: 'TechSupply Inc',
          email: 'sales@techsupply.com',
          phone: '+1-555-0200',
          address: '456 Tech Boulevard, Silicon Valley, SV 67890',
          performanceScore: 88.0,
          onTimeDeliveryRate: 90.0,
          qualityScore: 85.0,
          invoiceAccuracy: 95.0,
          isActive: true,
        },
        {
          code: 'GLOBAL003',
          name: 'Global Parts Ltd',
          email: 'info@globalparts.com',
          phone: '+1-555-0300',
          address: '789 Commerce Street, Trade City, TC 11111',
          performanceScore: 85.5,
          onTimeDeliveryRate: 88.0,
          qualityScore: 82.0,
          invoiceAccuracy: 92.0,
          isActive: true,
        },
      ]);

      logger.info(`Created ${vendors.length} vendors`);
    } else {
      vendors = await vendorRepo.find();
      logger.info('Vendors already exist, skipping vendor creation');
    }

    // Create purchase orders with items only if they don't exist
    if (existingPOs === 0) {
      purchaseOrders = [];
      
      for (let i = 1; i <= 10; i++) {
        const vendor = vendors[i % vendors.length];
        const assignedDate = new Date(2025, 0, i);
        const dueDate = new Date(assignedDate);
        dueDate.setDate(dueDate.getDate() + 30);
        
        const po = await poRepo.save({
          poNumber: `PO-2025-${String(i).padStart(5, '0')}`,
          vendorId: vendor.id,
          sourceLocation: `Warehouse ${String.fromCharCode(64 + (i % 3) + 1)}`,
          assignedDate,
          dueDate,
          status: i <= 5 ? PurchaseOrderStatus.ACTIVE : PurchaseOrderStatus.RECEIVED,
          totalAmount: 0,
          currency: 'USD',
          warehouse: `Warehouse ${String.fromCharCode(64 + (i % 3) + 1)}`,
          createdById: users[0].id,
        });

        // Create items for this PO
        const items = [];
        const itemCount = Math.floor(Math.random() * 3) + 1;
        
        for (let j = 1; j <= itemCount; j++) {
          const quantity = Math.floor(Math.random() * 50) + 10;
          const unitPrice = Math.floor(Math.random() * 500) + 50;
          const totalPrice = quantity * unitPrice;
          const receivedQuantity = i > 5 ? quantity : 0;

          const item = await poItemRepo.save({
            purchaseOrderId: po.id,
            description: `Item ${j} - ${vendor.name} Product`,
            quantity,
            receivedQuantity,
            unitPrice,
            totalPrice,
          });
          items.push(item);
        }

        // Update PO total amount
        const totalAmount = items.reduce((sum, item) => sum + Number(item.totalPrice), 0);
        po.totalAmount = totalAmount;
        await poRepo.save(po);

        purchaseOrders.push(po);
      }

      logger.info(`Created ${purchaseOrders.length} purchase orders with items`);
    } else {
      purchaseOrders = await poRepo.find();
      logger.info('Purchase orders already exist, skipping PO creation');
    }

    // Create goods receipts for received POs only if they don't exist
    if (existingGRs === 0) {
      const goodsReceipts = [];
      const receivedPOs = purchaseOrders.filter(po => po.status === PurchaseOrderStatus.RECEIVED);
      
      for (const po of receivedPOs) {
        const deliveryDate = new Date(po.assignedDate);
        deliveryDate.setDate(deliveryDate.getDate() + 25);
        
        const gr: GoodsReceipt = await grRepo.save({
          purchaseOrderId: po.id,
          deliveryDate,
          quantityReceived: Math.floor(Math.random() * 100) + 50,
          conditionNotes: 'Items received in good condition',
          deliveryReference: `DEL-2025-${String(goodsReceipts.length + 1).padStart(4, '0')}`,
          receivedById: users[0].id,
          status: Math.random() > 0.3 ? GoodsReceiptStatus.QC_PASSED : GoodsReceiptStatus.PENDING_QC,
        });
        
        goodsReceipts.push(gr);
      }

      logger.info(`Created ${goodsReceipts.length} goods receipts`);
    } else {
      logger.info('Goods receipts already exist, skipping GR creation');
    }

    logger.info('Database seeding completed successfully!');
  } catch (error) {
    logger.error('Error seeding database:', error);
    throw error;
  }
};
