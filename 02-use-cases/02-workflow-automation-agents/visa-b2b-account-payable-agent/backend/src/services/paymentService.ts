import { getDataSource } from '../config/database';
import { Invoice, PaymentStatus } from '../entities/Invoice';
import { Payment } from '../entities/Payment';
import { Between, LessThanOrEqual, MoreThanOrEqual, In, Like } from 'typeorm';
import { S3Service } from './s3Service';
import { decryptCardData } from '../utils/cardEncryption';

const s3Service = new S3Service();

interface PaymentQueryParams {
  page: number;
  limit: number;
  status?: string[];
  vendorId?: string;
  dueDateStart?: string;
  dueDateEnd?: string;
  amountMin?: number;
  amountMax?: number;
  overdueOnly?: boolean;
  search?: string;
}

interface PaymentSummary {
  totalPendingPayments: number;
  totalPaymentAmount: number;
  overduePayments: number;
  paymentsThisWeek: number;
  paymentsByStatus: {
    ready: number;
    scheduled: number;
    processing: number;
    sent: number;
    paid: number;
    failed: number;
    cancelled: number;
  };
}

class PaymentService {
  private async getInvoiceRepository() {
    const dataSource = await getDataSource();
    return dataSource.getRepository(Invoice);
  }

  private async getPaymentRepository() {
    const dataSource = await getDataSource();
    return dataSource.getRepository(Payment);
  }

  /**
   * Map payment status from Invoice to Payment UI format
   */
  private mapPaymentStatus(status: PaymentStatus): string {
    const statusMap: Record<PaymentStatus, string> = {
      [PaymentStatus.PENDING]: 'ready',
      [PaymentStatus.PROCESSING]: 'processing',
      [PaymentStatus.GENERATED]: 'scheduled',
      [PaymentStatus.SENT]: 'sent',
      [PaymentStatus.PAID]: 'paid',
      [PaymentStatus.FAILED]: 'failed',
      [PaymentStatus.CANCELLED]: 'cancelled',
    };
    return statusMap[status] || 'ready';
  }

  /**
   * Transform Invoice entity to Payment format for frontend
   */
  private transformInvoiceToPayment(invoice: Invoice) {
    return {
      id: invoice.id,
      invoiceId: invoice.id,
      invoiceNumber: invoice.invoiceNumber,
      vendor: invoice.vendor
        ? {
            id: invoice.vendor.id,
            code: invoice.vendor.code || '',
            name: invoice.vendor.name,
            email: invoice.vendor.email || undefined,
            phone: invoice.vendor.phone || undefined,
          }
        : {
            id: invoice.vendorId || '',
            code: '',
            name: 'Unknown Vendor',
          },
      vendorId: invoice.vendorId || '',
      invoiceDate: invoice.invoiceDate instanceof Date 
        ? invoice.invoiceDate.toISOString().split('T')[0]
        : String(invoice.invoiceDate),
      dueDate: invoice.dueDate
        ? (invoice.dueDate instanceof Date 
            ? invoice.dueDate.toISOString().split('T')[0]
            : String(invoice.dueDate))
        : new Date().toISOString().split('T')[0],
      amount: parseFloat(invoice.totalAmount.toString()),
      currency: invoice.currency,
      paymentStatus: this.mapPaymentStatus(invoice.paymentStatus),
      scheduledDate: null,
      sentDate: null,
      paidDate: null,
      paymentReference: null,
      iso20022FileKey: invoice.iso20022FileKey,
      iso20022FileMetadata: invoice.iso20022FileKey
        ? {
            fileName: invoice.iso20022FileKey.split('/').pop() || '',
            fileSize: 0,
            generatedAt: invoice.updatedAt.toISOString(),
            transactionCount: 1,
            messageId: `MSG-${invoice.invoiceNumber}`,
          }
        : null,
      extractedData: invoice.extractedData,
      createdAt: invoice.createdAt.toISOString(),
      updatedAt: invoice.updatedAt.toISOString(),
    };
  }

  /**
   * Get paginated list of payments with filters
   */
  async getPayments(params: PaymentQueryParams) {
    const invoiceRepository = await this.getInvoiceRepository();
    
    const {
      page,
      limit,
      status,
      vendorId,
      dueDateStart,
      dueDateEnd,
      amountMin,
      amountMax,
      overdueOnly,
      search,
    } = params;

    const skip = (page - 1) * limit;

    // Build where clause
    const where: any = {};

    // Status filter - map UI status to DB status
    if (status && status.length > 0) {
      const dbStatuses: PaymentStatus[] = [];
      status.forEach((s) => {
        switch (s) {
          case 'ready':
            dbStatuses.push(PaymentStatus.PENDING);
            break;
          case 'scheduled':
            dbStatuses.push(PaymentStatus.GENERATED);
            break;
          case 'processing':
            dbStatuses.push(PaymentStatus.PROCESSING);
            break;
          case 'sent':
            dbStatuses.push(PaymentStatus.SENT);
            break;
          case 'paid':
            dbStatuses.push(PaymentStatus.PAID);
            break;
          case 'failed':
            dbStatuses.push(PaymentStatus.FAILED);
            break;
          case 'cancelled':
            dbStatuses.push(PaymentStatus.CANCELLED);
            break;
        }
      });
      if (dbStatuses.length > 0) {
        where.paymentStatus = In(dbStatuses);
      }
    }

    if (vendorId) {
      where.vendorId = vendorId;
    }

    if (dueDateStart && dueDateEnd) {
      where.dueDate = Between(new Date(dueDateStart), new Date(dueDateEnd));
    } else if (dueDateStart) {
      where.dueDate = MoreThanOrEqual(new Date(dueDateStart));
    } else if (dueDateEnd) {
      where.dueDate = LessThanOrEqual(new Date(dueDateEnd));
    }

    if (amountMin !== undefined && amountMax !== undefined) {
      where.totalAmount = Between(amountMin, amountMax);
    } else if (amountMin !== undefined) {
      where.totalAmount = MoreThanOrEqual(amountMin);
    } else if (amountMax !== undefined) {
      where.totalAmount = LessThanOrEqual(amountMax);
    }

    if (overdueOnly) {
      where.dueDate = LessThanOrEqual(new Date());
      where.paymentStatus = In([
        PaymentStatus.PENDING,
        PaymentStatus.PROCESSING,
        PaymentStatus.GENERATED,
      ]);
    }

    if (search) {
      where.invoiceNumber = Like(`%${search}%`);
    }

    const [invoices, total] = await invoiceRepository.findAndCount({
      where,
      relations: ['vendor'],
      skip,
      take: limit,
      order: { createdAt: 'DESC' },
    });

    const payments = invoices.map((invoice) =>
      this.transformInvoiceToPayment(invoice)
    );

    return {
      data: payments,
      pagination: {
        page,
        limit,
        total,
      },
    };
  }

  /**
   * Get single payment by ID
   */
  async getPaymentById(id: string) {
    const invoiceRepository = await this.getInvoiceRepository();
    const invoice = await invoiceRepository.findOne({
      where: { id },
      relations: ['vendor', 'purchaseOrder', 'goodsReceipt'],
    });

    if (!invoice) {
      return null;
    }

    return this.transformInvoiceToPayment(invoice);
  }

  /**
   * Get payment summary statistics
   */
  async getPaymentSummary(): Promise<PaymentSummary> {
    const invoiceRepository = await this.getInvoiceRepository();
    const today = new Date();
    const weekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);

    // Total pending payments (not paid/cancelled)
    const totalPending = await invoiceRepository.count({
      where: {
        paymentStatus: In([
          PaymentStatus.PENDING,
          PaymentStatus.PROCESSING,
          PaymentStatus.GENERATED,
          PaymentStatus.SENT,
        ]),
      },
    });

    // Total payment amount (pending)
    const pendingInvoices = await invoiceRepository.find({
      where: {
        paymentStatus: In([
          PaymentStatus.PENDING,
          PaymentStatus.PROCESSING,
          PaymentStatus.GENERATED,
          PaymentStatus.SENT,
        ]),
      },
      select: ['totalAmount'],
    });

    const totalAmount = pendingInvoices.reduce(
      (sum, inv) => sum + parseFloat(inv.totalAmount.toString()),
      0
    );

    // Overdue payments
    const overdue = await invoiceRepository.count({
      where: {
        dueDate: LessThanOrEqual(today),
        paymentStatus: In([
          PaymentStatus.PENDING,
          PaymentStatus.PROCESSING,
          PaymentStatus.GENERATED,
        ]),
      },
    });

    // Payments this week
    const thisWeek = await invoiceRepository.count({
      where: {
        createdAt: MoreThanOrEqual(weekAgo),
      },
    });

    // Count by status
    const statusCounts = await Promise.all([
      invoiceRepository.count({
        where: { paymentStatus: PaymentStatus.PENDING },
      }),
      invoiceRepository.count({
        where: { paymentStatus: PaymentStatus.GENERATED },
      }),
      invoiceRepository.count({
        where: { paymentStatus: PaymentStatus.PROCESSING },
      }),
      invoiceRepository.count({
        where: { paymentStatus: PaymentStatus.SENT },
      }),
      invoiceRepository.count({
        where: { paymentStatus: PaymentStatus.PAID },
      }),
      invoiceRepository.count({
        where: { paymentStatus: PaymentStatus.FAILED },
      }),
      invoiceRepository.count({
        where: { paymentStatus: PaymentStatus.CANCELLED },
      }),
    ]);

    return {
      totalPendingPayments: totalPending,
      totalPaymentAmount: totalAmount,
      overduePayments: overdue,
      paymentsThisWeek: thisWeek,
      paymentsByStatus: {
        ready: statusCounts[0],
        scheduled: statusCounts[1],
        processing: statusCounts[2],
        sent: statusCounts[3],
        paid: statusCounts[4],
        failed: statusCounts[5],
        cancelled: statusCounts[6],
      },
    };
  }

  /**
   * Get signed S3 URL for ISO 20022 file download
   * Note: The lambda outputs to OUTPUT_BUCKET (iso20022-output-files by default)
   * but the backend S3Service uses AWS_S3_BUCKET from .env
   * Make sure these match or update your .env to point to the lambda's output bucket
   */
  async getISO20022FileUrl(id: string): Promise<string | null> {
    const invoiceRepository = await this.getInvoiceRepository();
    const invoice = await invoiceRepository.findOne({
      where: { id },
      select: ['iso20022FileKey'],
    });

    if (!invoice || !invoice.iso20022FileKey) {
      return null;
    }

    // Generate signed URL for S3 download
    // The iso20022FileKey should be the full S3 key like "iso20022/20250117_143022_pain001_abc12345.xml"
    const url = await s3Service.getSignedUrl(invoice.iso20022FileKey);
    return url;
  }

  /**
   * Get invoice data for payment processing
   * Returns invoice with vendor information needed by payment agent
   */
  async getInvoiceDataForPayment(invoiceId: string) {
    const invoiceRepository = await this.getInvoiceRepository();
    
    const invoice = await invoiceRepository.findOne({
      where: { id: invoiceId },
      relations: ['vendor'],
    });

    if (!invoice) {
      throw new Error(`Invoice ${invoiceId} not found`);
    }

    // Ensure dueDate is a Date object
    const dueDate = invoice.dueDate 
      ? (invoice.dueDate instanceof Date ? invoice.dueDate : new Date(invoice.dueDate))
      : null;

    return {
      id: invoice.id,
      invoice_number: invoice.invoiceNumber,
      amount: parseFloat(invoice.totalAmount.toString()),
      currency: invoice.currency || 'USD',
      due_date: dueDate?.toISOString(),
      supplier_id: invoice.vendor?.id || invoice.vendorId,
      supplier_name: invoice.vendor?.name || 'Unknown Vendor',
      preferred_payment_method: 'CARD', // Supplier accepts cards
      accepts_virtual_cards: true, // Supplier accepts virtual cards
      extracted_data: invoice.extractedData, // Include full extracted data for ISO20022 generation
    };
  }

  /**
   * Create payment record before calling agent
   * Returns payment ID
   */
  async createPaymentRecord(
    invoiceId: string,
    paymentMethod: string,
    agentReasoning: string,
    amount: number,
    currency: string = 'USD'
  ): Promise<string> {
    const paymentRepository = await this.getPaymentRepository();
    
    const payment = paymentRepository.create({
      invoiceId,
      paymentMethod: paymentMethod as any,
      status: 'pending' as any,
      agentReasoning,
      amount,
      currency,
      createdAt: new Date(),
      updatedAt: new Date(),
    });

    await paymentRepository.save(payment);
    console.log(`Payment record created: ${payment.id}`);
    
    return payment.id;
  }

  /**
   * Update payment status after agent response
   */
  async updatePaymentStatus(
    paymentId: string,
    status: string,
    options?: {
      virtualCardId?: string;
      transactionId?: string;
      errorMessage?: string;
      paymentMethod?: string;
      agentReasoning?: string;
    }
  ): Promise<void> {
    const paymentRepository = await this.getPaymentRepository();
    
    const updateData: any = {
      status,
      updatedAt: new Date(),
    };

    if (options?.virtualCardId) {
      updateData.virtualCardId = options.virtualCardId;
    }

    if (options?.transactionId) {
      updateData.transactionId = options.transactionId;
    }

    if (options?.paymentMethod) {
      updateData.paymentMethod = options.paymentMethod;
    }

    if (options?.agentReasoning) {
      updateData.agentReasoning = options.agentReasoning;
    }

    if (status === 'completed') {
      updateData.completedAt = new Date();
    }

    await paymentRepository.update(paymentId, updateData);
    console.log(`Payment ${paymentId} status updated to ${status}`);
  }

  /**
   * Store encrypted card details from agent response
   */
  async storeCardDetails(paymentId: string, cardData: any): Promise<void> {
    const { encryptCardData } = await import('../utils/cardEncryption');
    const paymentRepository = await this.getPaymentRepository();
    
    console.log('Storing card details for payment:', paymentId);
    console.log('Card data received:', JSON.stringify(cardData, null, 2));
    
    const cardNumber = cardData.account_number || cardData.accountNumber || '';
    const cvv = cardData.cvv || '';
    let expiry = cardData.expiration_date || cardData.expirationDate || '';
    const trackingNumber = cardData.tracking_number || cardData.trackingNumber || '';

    // Convert expiry from YYYY-MM-DD to MM/YYYY format if needed
    if (expiry && expiry.includes('-')) {
      // Format: 2025-12-31 -> 12/2025
      const parts = expiry.split('-');
      if (parts.length === 3) {
        expiry = `${parts[1]}/${parts[0]}`; // MM/YYYY
      }
    }

    console.log(`Extracted values: cardNumber="${cardNumber}", cvv="${cvv}", expiry="${expiry}", trackingNumber="${trackingNumber}"`);

    // Validate required fields before encryption
    if (!cardNumber || !cvv) {
      throw new Error(`Missing required card details: cardNumber=${!!cardNumber}, cvv=${!!cvv}`);
    }

    // Encrypt sensitive data
    const encryptedCardNumber = await encryptCardData(cardNumber);
    const encryptedCvv = await encryptCardData(cvv);

    // Get last 4 digits for display
    const last4 = cardNumber.slice(-4);
    const maskedCard = `**** ${last4}`;

    const KMS_KEY_ID = process.env.KMS_KEY_ID || 'alias/rtp-overlay-payment-cards';

    await paymentRepository.update(paymentId, {
      virtualCardNumber: maskedCard,
      virtualCardNumberEncrypted: encryptedCardNumber,
      virtualCardCvvEncrypted: encryptedCvv,
      virtualCardExpiry: expiry,
      trackingNumber,
      encryptionKeyId: KMS_KEY_ID,
      status: 'card_issued' as any,
      updatedAt: new Date(),
    });

    console.log(`Card details stored for payment ${paymentId}`);
    console.log(`  Card: ${maskedCard}`);
    console.log(`  Expiry: ${expiry}`);
    console.log(`  Tracking: ${trackingNumber}`);
  }

  /**
   * Update invoice with payment information
   */
  async updateInvoiceWithPayment(
    invoiceId: string,
    paymentId: string,
    paymentMethod: string,
    iso20022FileKey?: string
  ): Promise<void> {
    const invoiceRepository = await this.getInvoiceRepository();
    
    const updateData: any = {
      paymentId,
      paymentMethod,
      updatedAt: new Date(),
    };

    // Add ISO20022 file key if provided
    if (iso20022FileKey) {
      updateData.iso20022FileKey = iso20022FileKey;
    }
    
    await invoiceRepository.update(invoiceId, updateData);

    console.log(`Invoice ${invoiceId} updated with payment ${paymentId}${iso20022FileKey ? ` and file key ${iso20022FileKey}` : ''}`);
  }

  /**
   * Task 6.3: Get decrypted card details for a payment
   * Returns full card number, CVV, and expiry
   */
  async getDecryptedCardDetails(paymentId: string) {
    const paymentRepository = await this.getPaymentRepository();
    
    const payment = await paymentRepository.findOne({
      where: { id: paymentId },
      relations: ['invoice'],
    });

    if (!payment) {
      return null;
    }

    // Check if payment has card details
    if (!payment.virtualCardNumberEncrypted || !payment.virtualCardCvvEncrypted) {
      return null;
    }

    try {
      // Decrypt card details
      const decryptedCardNumber = await decryptCardData(payment.virtualCardNumberEncrypted);
      const decryptedCvv = await decryptCardData(payment.virtualCardCvvEncrypted);

      return {
        paymentId: payment.id,
        invoiceId: payment.invoiceId,
        invoiceNumber: payment.invoice?.invoiceNumber,
        cardNumber: decryptedCardNumber,
        cvv: decryptedCvv,
        expiry: payment.virtualCardExpiry,
        trackingNumber: payment.trackingNumber,
        amount: parseFloat(payment.amount.toString()),
        currency: payment.currency,
        status: payment.status,
        paymentMethod: payment.paymentMethod,
        // Also return masked version for display
        maskedCardNumber: payment.virtualCardNumber,
      };
    } catch (error) {
      console.error('Error decrypting card details:', error);
      throw new Error('Failed to decrypt card details');
    }
  }

  /**
   * Validate 3-way match before processing payment
   * Checks that invoice has both PO and GR matched
   */
  async validateThreeWayMatch(invoiceId: string): Promise<{
    canPay: boolean;
    reason?: string;
    warnings?: string[];
  }> {
    const invoiceRepo = await this.getInvoiceRepository();
    
    const invoice = await invoiceRepo.findOne({
      where: { id: invoiceId },
      relations: ['purchaseOrder', 'goodsReceipt', 'vendor'],
    });

    if (!invoice) {
      return {
        canPay: false,
        reason: 'Invoice not found',
      };
    }

    const warnings: string[] = [];

    // Check if invoice has PO match
    if (!invoice.hasPoMatch || !invoice.purchaseOrderId) {
      return {
        canPay: false,
        reason: 'Invoice must be matched to a Purchase Order before payment',
      };
    }

    // Check if invoice has GR match (3-way match required)
    if (!invoice.goodsReceiptId) {
      return {
        canPay: false,
        reason: 'Invoice must be matched to a Goods Receipt before payment (3-way match required)',
      };
    }

    // Check match status
    if (invoice.matchStatus === 'MISMATCHED') {
      warnings.push('Invoice has mismatched quantities or amounts');
    }

    // Check for high variance
    if (invoice.hasVarianceWarning) {
      warnings.push(`Invoice amount variance exceeds tolerance: ${invoice.variancePercentage}%`);
    }

    // Check validation flags
    if (invoice.validationFlags && invoice.validationFlags.length > 0) {
      warnings.push(`Validation issues: ${invoice.validationFlags.join(', ')}`);
    }

    // Allow payment if 3-way match exists, even with warnings
    return {
      canPay: true,
      warnings: warnings.length > 0 ? warnings : undefined,
    };
  }
}

export const paymentService = new PaymentService();
