import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  UpdateDateColumn,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { Vendor } from './Vendor';
import { PurchaseOrder } from './PurchaseOrder';
import { GoodsReceipt } from './GoodsReceipt';
import { QCRecord } from './QCRecord';
import { Payment } from './Payment';

export enum PaymentStatus {
  PENDING = 'pending',
  PROCESSING = 'processing',
  GENERATED = 'generated',
  SENT = 'sent',
  PAID = 'paid',
  FAILED = 'failed',
  CANCELLED = 'cancelled',
}

export enum MatchStatus {
  MATCHED = 'MATCHED',
  MISMATCHED = 'MISMATCHED',
  PENDING = 'PENDING',
}

@Entity('invoices')
export class Invoice {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ name: 'invoice_number', unique: true })
  invoiceNumber: string;

  @Column({ name: 'invoice_date', type: 'date' })
  invoiceDate: Date;

  @Column({ name: 'due_date', type: 'date', nullable: true })
  dueDate: Date;

  @ManyToOne(() => Vendor, { nullable: true })
  @JoinColumn({ name: 'vendor_id' })
  vendor: Vendor;

  @Column({ name: 'vendor_id', nullable: true })
  vendorId: string;

  @ManyToOne(() => PurchaseOrder, { nullable: true })
  @JoinColumn({ name: 'purchase_order_id' })
  purchaseOrder: PurchaseOrder;

  @Column({ name: 'purchase_order_id', nullable: true })
  purchaseOrderId: string;

  @ManyToOne(() => GoodsReceipt, { nullable: true })
  @JoinColumn({ name: 'goods_receipt_id' })
  goodsReceipt: GoodsReceipt;

  @Column({ name: 'goods_receipt_id', nullable: true })
  goodsReceiptId: string;

  @Column({ length: 3, default: 'USD' })
  currency: string;

  @Column({ type: 'decimal', precision: 12, scale: 2 })
  subtotal: number;

  @Column({ name: 'tax_amount', type: 'decimal', precision: 12, scale: 2 })
  taxAmount: number;

  @Column({ name: 'total_amount', type: 'decimal', precision: 12, scale: 2 })
  totalAmount: number;

  @Column({
    type: 'enum',
    enum: PaymentStatus,
    default: PaymentStatus.PENDING,
  })
  paymentStatus: PaymentStatus;

  @Column({ name: 'source_file_key', nullable: true })
  sourceFileKey: string;

  @Column({ name: 'iso20022_file_key', nullable: true })
  iso20022FileKey: string;

  @Column({ name: 'extracted_data', type: 'jsonb' })
  extractedData: any;

  @Column({
    name: 'variance_percentage',
    type: 'decimal',
    precision: 5,
    scale: 2,
    nullable: true,
  })
  variancePercentage: number;

  @Column({ name: 'has_po_match', default: false })
  hasPoMatch: boolean;

  @Column({ name: 'has_variance_warning', default: false })
  hasVarianceWarning: boolean;

  @Column({
    name: 'match_status',
    type: 'varchar',
    length: 20,
    default: 'PENDING',
  })
  matchStatus: string;

  @Column({ name: 'validation_flags', type: 'jsonb', default: () => "'[]'" })
  validationFlags: string[];

  @ManyToOne(() => QCRecord, { nullable: true })
  @JoinColumn({ name: 'matched_qc_id' })
  matchedQC: QCRecord;

  @Column({ name: 'matched_qc_id', nullable: true })
  matchedQCId: string;

  @Column({ name: 'payment_method', nullable: true })
  paymentMethod: string;

  @ManyToOne(() => Payment, { nullable: true })
  @JoinColumn({ name: 'payment_id' })
  payment: Payment;

  @Column({ name: 'payment_id', nullable: true })
  paymentId: string;

  @CreateDateColumn({ name: 'created_at' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at' })
  updatedAt: Date;
}
