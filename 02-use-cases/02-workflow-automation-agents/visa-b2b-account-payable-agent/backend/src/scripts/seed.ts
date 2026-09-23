import 'reflect-metadata';
import { AppDataSource } from '../data-source';
import { seedPurchaseOrders } from '../seeders/purchaseOrderSeeder';
import { seedGoodsReceipts } from '../seeders/goodsReceiptSeeder';

async function runSeed() {
  try {
    console.log('🌱 Starting database seeding...');
    
    await AppDataSource.initialize();
    console.log('✅ Database connection established');

    await seedPurchaseOrders(AppDataSource);
    await seedGoodsReceipts(AppDataSource);
    
    console.log('🎉 Seeding completed successfully!');
    process.exit(0);
  } catch (error) {
    console.error('❌ Error during seeding:', error);
    process.exit(1);
  }
}

runSeed();
