import { DataSource } from 'typeorm';
import { GoodsReceipt, GoodsReceiptStatus } from '../entities/GoodsReceipt';
import { PurchaseOrder } from '../entities/PurchaseOrder';
import { User } from '../entities/User';

export async function seedGoodsReceipts(dataSource: DataSource): Promise<void> {
  const grRepo = dataSource.getRepository(GoodsReceipt);
  const poRepo = dataSource.getRepository(PurchaseOrder);
  const userRepo = dataSource.getRepository(User);

  // Get existing POs
  const pos = await poRepo.find({ take: 5 });
  
  if (pos.length === 0) {
    console.log('⚠️  No purchase orders found. Please seed POs first.');
    return;
  }

  // Get a user to assign as receiver (or use first user)
  const users = await userRepo.find({ take: 1 });
  const receiverId = users[0]?.id;

  if (!receiverId) {
    console.log('⚠️  No users found. Please seed users first.');
    return;
  }

  // Create goods receipts for some POs
  const goodsReceipts = [
    {
      purchaseOrderId: pos[0].id,
      deliveryDate: new Date('2025-08-01'),
      quantityReceived: 800, // Partial receipt
      conditionNotes: 'Good condition, minor packaging damage on 2 units',
      deliveryReference: 'DEL-2025-001',
      receivedById: receiverId,
      status: GoodsReceiptStatus.QC_PASSED,
    },
    {
      purchaseOrderId: pos[1].id,
      deliveryDate: new Date('2025-08-05'),
      quantityReceived: 50, // Full receipt
      conditionNotes: 'All items in excellent condition',
      deliveryReference: 'DEL-2025-002',
      receivedById: receiverId,
      status: GoodsReceiptStatus.PENDING_QC,
    },
    {
      purchaseOrderId: pos[2].id,
      deliveryDate: new Date('2025-07-30'),
      quantityReceived: 1500, // Partial receipt
      conditionNotes: 'Received in good condition',
      deliveryReference: 'DEL-2025-003',
      receivedById: receiverId,
      status: GoodsReceiptStatus.QC_PASSED,
    },
  ];

  await grRepo.save(goodsReceipts);

  console.log(`✅ ${goodsReceipts.length} goods receipts seeded successfully`);
  console.log('   Linked to POs:', pos.slice(0, 3).map(po => po.poNumber).join(', '));
}
