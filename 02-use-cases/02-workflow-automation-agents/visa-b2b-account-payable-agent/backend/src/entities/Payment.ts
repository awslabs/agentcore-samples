import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  UpdateDateColumn,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { Invoice } from './Invoice';

export enum PaymentMethod {
  VISA_B2B = 'visa_b2b',
  ISO20022 = 'iso20022',
}

export enum PaymentTransactionStatus {
  PENDING = 'pending',
  CARD_ISSUED = 'card_issued',
  SUBMITTED = 'submitted',
  COMPLETED = 'completed',
  FAILED = 'failed',
}

@Entity('payments')
export class Payment {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @ManyToOne(() => Invoice)
  @JoinColumn({ name: 'invoice_id' })
  invoice: Invoice;

  @Column({ name: 'invoice_id' })
  invoiceId: string;

  @Column({
    name: 'payment_method',
    type: 'varchar',
    length: 50,
  })
  paymentMethod: PaymentMethod;

  @Column({ name: 'virtual_card_id', nullable: true })
  virtualCardId: string;

  @Column({ name: 'payment_id', nullable: true })
  paymentId: string;

  @Column({ name: 'transaction_id', nullable: true })
  transactionId: string;

  @Column({ type: 'decimal', precision: 15, scale: 2 })
  amount: number;

  @Column({ length: 3, default: 'USD' })
  currency: string;

  @Column({
    type: 'varchar',
    length: 50,
    default: PaymentTransactionStatus.PENDING,
  })
  status: PaymentTransactionStatus;

  @Column({ name: 'agent_reasoning', type: 'text', nullable: true })
  agentReasoning: string;

  @CreateDateColumn({ name: 'created_at' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at' })
  updatedAt: Date;

  @Column({ name: 'completed_at', type: 'timestamp', nullable: true })
  completedAt: Date;

  // Virtual Card Fields
  @Column({ name: 'virtual_card_number', type: 'varchar', length: 20, nullable: true })
  virtualCardNumber: string; // Last 4 digits for display (e.g., "**** 1234")

  @Column({ name: 'virtual_card_number_encrypted', type: 'text', nullable: true })
  virtualCardNumberEncrypted: string; // Full encrypted card number

  @Column({ name: 'virtual_card_cvv_encrypted', type: 'text', nullable: true })
  virtualCardCvvEncrypted: string; // Encrypted CVV

  @Column({ name: 'virtual_card_expiry', type: 'varchar', length: 7, nullable: true })
  virtualCardExpiry: string; // MM/YYYY format

  @Column({ name: 'tracking_number', type: 'varchar', length: 50, nullable: true })
  trackingNumber: string; // Visa tracking number

  @Column({ name: 'encryption_key_id', type: 'varchar', length: 255, nullable: true })
  encryptionKeyId: string; // KMS key ID for key rotation

  @Column({ name: 'supplier_accessed_at', type: 'timestamp', nullable: true })
  supplierAccessedAt: Date; // When supplier viewed card

  @Column({ name: 'supplier_access_count', type: 'int', default: 0 })
  supplierAccessCount: number; // How many times supplier accessed
}
