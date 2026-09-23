import { MigrationInterface, QueryRunner } from 'typeorm';

export class AllowNullPOInGoodsReceipts20251030000001 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    // Allow purchase_order_id to be null in goods_receipts table
    await queryRunner.query(`
      ALTER TABLE goods_receipts 
      ALTER COLUMN purchase_order_id DROP NOT NULL
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    // Revert: make purchase_order_id NOT NULL again
    await queryRunner.query(`
      ALTER TABLE goods_receipts 
      ALTER COLUMN purchase_order_id SET NOT NULL
    `);
  }
}
