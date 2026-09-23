import { MigrationInterface, QueryRunner, Table, TableForeignKey, TableIndex } from 'typeorm';

export class CreatePaymentsTable20251202000001 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.createTable(
      new Table({
        name: 'payments',
        columns: [
          {
            name: 'id',
            type: 'uuid',
            isPrimary: true,
            default: 'gen_random_uuid()',
          },
          {
            name: 'invoice_id',
            type: 'uuid',
          },
          {
            name: 'payment_method',
            type: 'varchar',
            length: '50',
          },
          {
            name: 'virtual_card_id',
            type: 'varchar',
            length: '100',
            isNullable: true,
          },
          {
            name: 'payment_id',
            type: 'varchar',
            length: '100',
            isNullable: true,
          },
          {
            name: 'transaction_id',
            type: 'varchar',
            length: '100',
            isNullable: true,
          },
          {
            name: 'amount',
            type: 'decimal',
            precision: 15,
            scale: 2,
          },
          {
            name: 'currency',
            type: 'varchar',
            length: '3',
            default: "'USD'",
          },
          {
            name: 'status',
            type: 'varchar',
            length: '50',
            default: "'pending'",
          },
          {
            name: 'agent_reasoning',
            type: 'text',
            isNullable: true,
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
          {
            name: 'completed_at',
            type: 'timestamp',
            isNullable: true,
          },
          // Virtual Card Fields
          {
            name: 'virtual_card_number',
            type: 'varchar',
            length: '20',
            isNullable: true,
            comment: 'Last 4 digits for display (e.g., "**** 1234")',
          },
          {
            name: 'virtual_card_number_encrypted',
            type: 'text',
            isNullable: true,
            comment: 'Full encrypted card number',
          },
          {
            name: 'virtual_card_cvv_encrypted',
            type: 'text',
            isNullable: true,
            comment: 'Encrypted CVV',
          },
          {
            name: 'virtual_card_expiry',
            type: 'varchar',
            length: '7',
            isNullable: true,
            comment: 'MM/YYYY format',
          },
          {
            name: 'tracking_number',
            type: 'varchar',
            length: '50',
            isNullable: true,
            comment: 'Visa tracking number',
          },
          {
            name: 'encryption_key_id',
            type: 'varchar',
            length: '255',
            isNullable: true,
            comment: 'KMS key ID for key rotation',
          },
          {
            name: 'supplier_accessed_at',
            type: 'timestamp',
            isNullable: true,
            comment: 'When supplier viewed card',
          },
          {
            name: 'supplier_access_count',
            type: 'int',
            default: 0,
            comment: 'How many times supplier accessed',
          },
        ],
      }),
      true
    );

    // Foreign key to invoices
    await queryRunner.createForeignKey(
      'payments',
      new TableForeignKey({
        columnNames: ['invoice_id'],
        referencedColumnNames: ['id'],
        referencedTableName: 'invoices',
        onDelete: 'CASCADE',
      })
    );

    // Indexes for common queries
    await queryRunner.createIndex(
      'payments',
      new TableIndex({
        name: 'idx_payments_invoice',
        columnNames: ['invoice_id'],
      })
    );

    await queryRunner.createIndex(
      'payments',
      new TableIndex({
        name: 'idx_payments_status',
        columnNames: ['status'],
      })
    );

    await queryRunner.createIndex(
      'payments',
      new TableIndex({
        name: 'idx_payments_method',
        columnNames: ['payment_method'],
      })
    );

    await queryRunner.createIndex(
      'payments',
      new TableIndex({
        name: 'idx_payments_tracking',
        columnNames: ['tracking_number'],
      })
    );

    await queryRunner.createIndex(
      'payments',
      new TableIndex({
        name: 'idx_payments_payment_id',
        columnNames: ['payment_id'],
      })
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.dropTable('payments');
  }
}
