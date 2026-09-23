import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  UpdateDateColumn,
  ManyToOne,
  OneToMany,
  JoinColumn,
} from 'typeorm';
import { PurchaseOrder } from './PurchaseOrder';
import { User } from './User';
import { ReceiptDocument } from './ReceiptDocument';

export enum GoodsReceiptStatus {
  PENDING_QC = 'pending_qc',
  QC_PASSED = 'qc_passed',
  QC_FAILED = 'qc_failed',
}

@Entity('goods_receipts')
export class GoodsReceipt {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @ManyToOne(() => PurchaseOrder, (po) => po.goodsReceipts, { nullable: true })
  @JoinColumn({ name: 'purchase_order_id' })
  purchaseOrder: PurchaseOrder;

  @Column({ name: 'purchase_order_id', nullable: true })
  purchaseOrderId: string;

  @Column({ name: 'delivery_date', type: 'date' })
  deliveryDate: Date;

  @Column({ name: 'quantity_received', type: 'int' })
  quantityReceived: number;

  @Column({ name: 'condition_notes', type: 'text', nullable: true })
  conditionNotes: string;

  @Column({ name: 'delivery_reference' })
  deliveryReference: string;

  @ManyToOne(() => User)
  @JoinColumn({ name: 'received_by' })
  receivedBy: User;

  @Column({ name: 'received_by' })
  receivedById: string;

  @Column({
    type: 'enum',
    enum: GoodsReceiptStatus,
    default: GoodsReceiptStatus.PENDING_QC,
  })
  status: GoodsReceiptStatus;

  @Column({ name: 'source_file_key', nullable: true })
  sourceFileKey: string;

  @Column({ name: 'material_description', nullable: true })
  materialDescription: string;

  @Column({ name: 'extracted_data', type: 'jsonb', nullable: true })
  extractedData: any;

  @Column({ name: 'confidence_scores', type: 'jsonb', nullable: true })
  confidenceScores: any;

  @CreateDateColumn({ name: 'created_at' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at' })
  updatedAt: Date;

  @OneToMany(() => ReceiptDocument, (doc) => doc.goodsReceipt, { cascade: true })
  documents: ReceiptDocument[];
}
