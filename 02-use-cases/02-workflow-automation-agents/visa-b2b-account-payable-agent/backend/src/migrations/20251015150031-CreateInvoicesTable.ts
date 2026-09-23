import { MigrationInterface, QueryRunner, Table, TableForeignKey, TableIndex } from 'typeorm';

export class CreateInvoicesTable20251015150031 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.createTable(
      new Table({
        name: 'invoices',
        columns: [
          {
            name: 'id',
            type: 'uuid',
            isPrimary: true,
            default: 'gen_random_uuid()',
          },
          {
            name: 'invoice_number',
            type: 'varchar',
            length: '100',
            isUnique: true,
          },
          {
            name: 'invoice_date',
            type: 'date',
          },
          {
            name: 'due_date',
            type: 'date',
            isNullable: true,
          },
          {
            name: 'vendor_id',
            type: 'uuid',
            isNullable: true,
          },
          {
            name: 'purchase_order_id',
            type: 'uuid',
            isNullable: true,
          },
          {
            name: 'goods_receipt_id',
            type: 'uuid',
            isNullable: true,
          },
          {
            name: 'currency',
            type: 'varchar',
            length: '3',
            default: "'USD'",
          },
          {
            name: 'subtotal',
            type: 'decimal',
            precision: 12,
            scale: 2,
          },
          {
            name: 'tax_amount',
            type: 'decimal',
            precision: 12,
            scale: 2,
          },
          {
            name: 'total_amount',
            type: 'decimal',
            precision: 12,
            scale: 2,
          },
          {
            name: 'payment_status',
            type: 'varchar',
            length: '20',
            default: "'pending'",
          },
          {
            name: 'source_file_key',
            type: 'varchar',
            length: '500',
            isNullable: true,
          },
          {
            name: 'iso20022_file_key',
            type: 'varchar',
            length: '500',
            isNullable: true,
          },
          {
            name: 'extracted_data',
            type: 'jsonb',
          },
          {
            name: 'variance_percentage',
            type: 'decimal',
            precision: 5,
            scale: 2,
            isNullable: true,
          },
          {
            name: 'has_po_match',
            type: 'boolean',
            default: false,
          },
          {
            name: 'has_variance_warning',
            type: 'boolean',
            default: false,
          },
          {
            name: 'created_at',
            type: 'timestamp',
            default: 'CURRENT_TIMESTAMP',
          },
          {
            name: 'updated_at',
            type: 'timestamp',
            default: 'CURRENT_TIMESTAMP',
          },
        ],
      }),
      true
    );

    // Foreign keys
    await queryRunner.createForeignKey(
      'invoices',
      new TableForeignKey({
        columnNames: ['vendor_id'],
        referencedColumnNames: ['id'],
        referencedTableName: 'vendors',
        onDelete: 'SET NULL',
      })
    );

    await queryRunner.createForeignKey(
      'invoices',
      new TableForeignKey({
        columnNames: ['purchase_order_id'],
        referencedColumnNames: ['id'],
        referencedTableName: 'purchase_orders',
        onDelete: 'SET NULL',
      })
    );

    await queryRunner.createForeignKey(
      'invoices',
      new TableForeignKey({
        columnNames: ['goods_receipt_id'],
        referencedColumnNames: ['id'],
        referencedTableName: 'goods_receipts',
        onDelete: 'SET NULL',
      })
    );

    // Indexes for common queries
    await queryRunner.createIndex(
      'invoices',
      new TableIndex({
        name: 'idx_invoices_vendor',
        columnNames: ['vendor_id'],
      })
    );

    await queryRunner.createIndex(
      'invoices',
      new TableIndex({
        name: 'idx_invoices_po',
        columnNames: ['purchase_order_id'],
      })
    );

    await queryRunner.createIndex(
      'invoices',
      new TableIndex({
        name: 'idx_invoices_gr',
        columnNames: ['goods_receipt_id'],
      })
    );

    await queryRunner.createIndex(
      'invoices',
      new TableIndex({
        name: 'idx_invoices_status',
        columnNames: ['payment_status'],
      })
    );

    await queryRunner.createIndex(
      'invoices',
      new TableIndex({
        name: 'idx_invoices_date',
        columnNames: ['invoice_date'],
      })
    );

    await queryRunner.createIndex(
      'invoices',
      new TableIndex({
        name: 'idx_invoices_po_match',
        columnNames: ['has_po_match'],
      })
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.dropTable('invoices');
  }
}
