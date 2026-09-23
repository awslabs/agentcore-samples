import { MigrationInterface, QueryRunner, TableColumn } from 'typeorm';

export class AddMatchValidationToInvoices20251030000003 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    // Add match_status column
    await queryRunner.addColumn(
      'invoices',
      new TableColumn({
        name: 'match_status',
        type: 'varchar',
        length: '20',
        default: "'PENDING'",
        isNullable: false,
      })
    );

    // Add validation_flags column (JSONB array)
    await queryRunner.addColumn(
      'invoices',
      new TableColumn({
        name: 'validation_flags',
        type: 'jsonb',
        default: "'[]'",
        isNullable: false,
      })
    );

    // Add matched_qc_id column
    await queryRunner.addColumn(
      'invoices',
      new TableColumn({
        name: 'matched_qc_id',
        type: 'uuid',
        isNullable: true,
      })
    );

    // Create index on match_status for filtering
    await queryRunner.query(`
      CREATE INDEX idx_invoices_match_status ON invoices(match_status);
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP INDEX idx_invoices_match_status;`);
    await queryRunner.dropColumn('invoices', 'matched_qc_id');
    await queryRunner.dropColumn('invoices', 'validation_flags');
    await queryRunner.dropColumn('invoices', 'match_status');
  }
}
