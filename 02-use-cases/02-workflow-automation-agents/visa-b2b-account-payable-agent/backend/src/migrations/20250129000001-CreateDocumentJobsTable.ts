import { MigrationInterface, QueryRunner, Table, TableIndex } from 'typeorm';

export class CreateDocumentJobsTable20250129000001 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.createTable(
      new Table({
        name: 'document_jobs',
        columns: [
          {
            name: 'id',
            type: 'uuid',
            isPrimary: true,
            default: 'gen_random_uuid()',
          },
          {
            name: 'job_type',
            type: 'varchar',
            length: '20',
          },
          {
            name: 'status',
            type: 'varchar',
            length: '20',
            default: "'UPLOADED'",
          },
          {
            name: 'source_file_key',
            type: 'varchar',
            length: '500',
          },
          {
            name: 'result_id',
            type: 'uuid',
            isNullable: true,
          },
          {
            name: 'error_message',
            type: 'text',
            isNullable: true,
          },
          {
            name: 'processing_time_ms',
            type: 'integer',
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

    // Indexes for common queries
    await queryRunner.createIndex(
      'document_jobs',
      new TableIndex({
        name: 'idx_jobs_status',
        columnNames: ['status'],
      })
    );

    await queryRunner.createIndex(
      'document_jobs',
      new TableIndex({
        name: 'idx_jobs_type',
        columnNames: ['job_type'],
      })
    );

    await queryRunner.createIndex(
      'document_jobs',
      new TableIndex({
        name: 'idx_jobs_created',
        columnNames: ['created_at'],
      })
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.dropTable('document_jobs');
  }
}
