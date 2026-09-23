import { MigrationInterface, QueryRunner, TableColumn } from 'typeorm';

export class AddPOEnhancedFields20251016000002 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    // Add dueDate to purchase_orders if it doesn't exist
    const poTable = await queryRunner.getTable('purchase_orders');
    const hasDueDate = poTable?.columns.find(col => col.name === 'due_date');
    
    if (!hasDueDate) {
      await queryRunner.addColumn(
        'purchase_orders',
        new TableColumn({
          name: 'due_date',
          type: 'date',
          isNullable: true,
        })
      );
    }

    // Add currency to purchase_orders if it doesn't exist
    const hasCurrency = poTable?.columns.find(col => col.name === 'currency');
    
    if (!hasCurrency) {
      await queryRunner.addColumn(
        'purchase_orders',
        new TableColumn({
          name: 'currency',
          type: 'varchar',
          length: '3',
          default: "'USD'",
        })
      );
    }

    // Add warehouse to purchase_orders if it doesn't exist
    const hasWarehouse = poTable?.columns.find(col => col.name === 'warehouse');
    
    if (!hasWarehouse) {
      await queryRunner.addColumn(
        'purchase_orders',
        new TableColumn({
          name: 'warehouse',
          type: 'varchar',
          length: '255',
          isNullable: true,
        })
      );
    }

    // Add receivedQuantity to purchase_order_items if it doesn't exist
    const itemTable = await queryRunner.getTable('purchase_order_items');
    const hasReceivedQty = itemTable?.columns.find(col => col.name === 'received_quantity');
    
    if (!hasReceivedQty) {
      await queryRunner.addColumn(
        'purchase_order_items',
        new TableColumn({
          name: 'received_quantity',
          type: 'decimal',
          precision: 15,
          scale: 2,
          default: 0,
        })
      );
    }

    // Update existing POs with calculated values
    await queryRunner.query(`
      UPDATE purchase_orders 
      SET 
        due_date = assigned_date + INTERVAL '30 days',
        currency = 'USD',
        warehouse = source_location
      WHERE due_date IS NULL
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.dropColumn('purchase_order_items', 'received_quantity');
    await queryRunner.dropColumn('purchase_orders', 'warehouse');
    await queryRunner.dropColumn('purchase_orders', 'currency');
    await queryRunner.dropColumn('purchase_orders', 'due_date');
  }
}
