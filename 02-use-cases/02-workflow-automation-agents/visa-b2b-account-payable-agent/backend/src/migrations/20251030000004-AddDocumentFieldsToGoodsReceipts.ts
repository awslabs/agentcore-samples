import { MigrationInterface, QueryRunner, TableColumn } from 'typeorm';

export class AddDocumentFieldsToGoodsReceipts20251030000004 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    // Add source_file_key column
    await queryRunner.addColumn(
      'goods_receipts',
      new TableColumn({
        name: 'source_file_key',
        type: 'varchar',
        length: '500',
        isNullable: true,
      })
    );

    // Add material_description column
    await queryRunner.addColumn(
      'goods_receipts',
      new TableColumn({
        name: 'material_description',
        type: 'varchar',
        length: '500',
        isNullable: true,
      })
    );

    // Add extracted_data column (JSONB)
    await queryRunner.addColumn(
      'goods_receipts',
      new TableColumn({
        name: 'extracted_data',
        type: 'jsonb',
        isNullable: true,
      })
    );

    // Add confidence_scores column (JSONB)
    await queryRunner.addColumn(
      'goods_receipts',
      new TableColumn({
        name: 'confidence_scores',
        type: 'jsonb',
        isNullable: true,
      })
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.dropColumn('goods_receipts', 'confidence_scores');
    await queryRunner.dropColumn('goods_receipts', 'extracted_data');
    await queryRunner.dropColumn('goods_receipts', 'material_description');
    await queryRunner.dropColumn('goods_receipts', 'source_file_key');
  }
}
