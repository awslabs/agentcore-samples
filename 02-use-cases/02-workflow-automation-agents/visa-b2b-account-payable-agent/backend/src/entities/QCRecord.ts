import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  ManyToOne,
  JoinColumn,
  CreateDateColumn,
  UpdateDateColumn,
} from 'typeorm';
import { PurchaseOrder } from './PurchaseOrder';
import { GoodsReceipt } from './GoodsReceipt';

@Entity('qc_records')
export class QCRecord {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ unique: true, name: 'qc_number' })
  qcNumber: string;

  @ManyToOne(() => PurchaseOrder)
  @JoinColumn({ name: 'po_id' })
  purchaseOrder: PurchaseOrder;

  @Column({ name: 'po_id' })
  purchaseOrderId: string;

  @ManyToOne(() => GoodsReceipt, { nullable: true })
  @JoinColumn({ name: 'gr_id' })
  goodsReceipt: GoodsReceipt;

  @Column({ name: 'gr_id', nullable: true })
  goodsReceiptId: string;

  @Column({ name: 'inspector_name' })
  inspectorName: string;

  @Column({ type: 'date', name: 'test_date' })
  testDate: Date;

  @Column({ name: 'lab_reference', nullable: true })
  labReference: string;

  @Column({ type: 'jsonb' })
  measurements: {
    [key: string]: {
      value: number;
      unit: string;
      status: 'PASS' | 'FAIL';
      specification?: {
        min?: number;
        max?: number;
      };
    };
  };

  @Column({ name: 'overall_status' })
  overallStatus: 'PASS' | 'FAIL';

  @Column({ type: 'text', nullable: true })
  notes: string;

  @CreateDateColumn({ name: 'created_at' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at' })
  updatedAt: Date;
}
