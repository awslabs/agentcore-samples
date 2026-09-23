import 'reflect-metadata';
import { AppDataSource } from '../data-source';
import { seedPurchaseOrders } from '../seeders/purchaseOrderSeeder';
import { seedGoodsReceipts } from '../seeders/goodsReceiptSeeder';

export const handler = async (event: any) => {
  try {
    console.log('🌱 Starting database seeding from Lambda...');
    
    await AppDataSource.initialize();
    console.log('✅ Database connection established');

    // Check if we should reseed POs or just add GRs
    const reseedAll = event.reseedAll || false;

    if (reseedAll) {
      console.log('Reseeding purchase orders...');
      await seedPurchaseOrders(AppDataSource);
    }

    console.log('Seeding goods receipts...');
    await seedGoodsReceipts(AppDataSource);
    
    await AppDataSource.destroy();
    
    return {
      statusCode: 200,
      body: JSON.stringify({
        message: 'Seeding completed successfully!',
        reseedAll,
      }),
    };
  } catch (error) {
    console.error('❌ Error during seeding:', error);
    
    return {
      statusCode: 500,
      body: JSON.stringify({
        message: 'Seeding failed',
        error: error instanceof Error ? error.message : 'Unknown error',
      }),
    };
  }
};
