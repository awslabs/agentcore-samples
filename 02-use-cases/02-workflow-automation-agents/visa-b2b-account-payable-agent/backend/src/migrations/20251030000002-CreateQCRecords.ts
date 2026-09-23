import { MigrationInterface, QueryRunner, Table, TableForeignKey } from 'typeorm';

export class CreateQCRecords20251030000002 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.createTable(
      new Table({
        name: 'qc_records',
        columns: [
          {
            name: 'id',
            type: 'uuid',
            isPrimary: true,
            generationStrategy: 'uuid',
            default: 'uuid_generate_v4()',
          },
          {
            name: 'qc_number',
            type: 'varchar',
            length: '50',
            isUnique: true,
            isNullable: false,
          },
          {
            name: 'po_id',
            type: 'uuid',
            isNullable: false,
          },
          {
            name: 'gr_id',
            type: 'uuid',
            isNullable: true,
          },
          {
            name: 'inspector_name',
            type: 'varchar',
            length: '100',
            isNullable: false,
          },
          {
            name: 'test_date',
            type: 'date',
            isNullable: false,
          },
          {
            name: 'lab_reference',
            type: 'varchar',
            length: '100',
            isNullable: true,
          },
          {
            name: 'measurements',
            type: 'jsonb',
            isNullable: false,
          },
          {
            name: 'overall_status',
            type: 'varchar',
            length: '20',
            isNullable: false,
          },
          {
            name: 'notes',
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
        ],
      }),
      true
    );

    // Add foreign key to purchase_orders
    await queryRunner.createForeignKey(
      'qc_records',
      new TableForeignKey({
        columnNames: ['po_id'],
        referencedColumnNames: ['id'],
        referencedTableName: 'purchase_orders',
        onDelete: 'CASCADE',
      })
    );

    // Add foreign key to goods_receipts
    await queryRunner.createForeignKey(
      'qc_records',
      new TableForeignKey({
        columnNames: ['gr_id'],
        referencedColumnNames: ['id'],
        referencedTableName: 'goods_receipts',
        onDelete: 'SET NULL',
      })
    );

    // Create indexes
    await queryRunner.query(`
      CREATE INDEX idx_qc_records_po_id ON qc_records(po_id);
      CREATE INDEX idx_qc_records_gr_id ON qc_records(gr_id);
      CREATE INDEX idx_qc_records_overall_status ON qc_records(overall_status);
      CREATE INDEX idx_qc_records_test_date ON qc_records(test_date);
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.dropTable('qc_records');
  }
}
