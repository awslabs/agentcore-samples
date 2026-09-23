import { getDataSource } from '../config/database';
import { Payment } from '../entities/Payment';
import { decryptCardData } from '../utils/cardEncryption';
import * as crypto from 'crypto';

class SupplierService {
  private async getPaymentRepository() {
    const dataSource = await getDataSource();
    return dataSource.getRepository(Payment);
  }

  /**
   * Task 7.1: Supplier login
   * Verify payment exists by Payment ID or Tracking Number
   * Generate simple session token
   */
  async login(identifier: string) {
    const paymentRepository = await this.getPaymentRepository();

    // Try to find payment by ID (UUID format) or tracking number
    let payment: Payment | null = null;

    // Check if identifier is UUID format
    const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    
    if (uuidRegex.test(identifier)) {
      // Search by payment ID
      payment = await paymentRepository.findOne({
        where: { id: identifier },
      });
    }

    // If not found by ID, try tracking number
    if (!payment) {
      payment = await paymentRepository.findOne({
        where: { trackingNumber: identifier },
      });
    }

    if (!payment) {
      return null;
    }

    // Generate simple session token (payment_id + random string + timestamp)
    // For demo purposes - in production, use JWT or proper session management
    const token = Buffer.from(
      `${payment.id}:${crypto.randomBytes(16).toString('hex')}:${Date.now()}`
    ).toString('base64');

    return {
      paymentId: payment.id,
      token,
    };
  }

  /**
   * Task 7.2: Get payment details for supplier
   * Decrypt card details and log supplier access
   */
  async getPaymentDetails(paymentId: string, token: string) {
    // Verify token (simple validation - extract payment ID from token)
    if (!this.verifyToken(token, paymentId)) {
      throw new Error('Invalid or expired token');
    }

    const paymentRepository = await this.getPaymentRepository();

    const payment = await paymentRepository.findOne({
      where: { id: paymentId },
      relations: ['invoice', 'invoice.vendor'],
    });

    if (!payment) {
      return null;
    }

    // Check if payment has card details
    if (!payment.virtualCardNumberEncrypted || !payment.virtualCardCvvEncrypted) {
      return {
        paymentId: payment.id,
        trackingNumber: payment.trackingNumber,
        amount: parseFloat(payment.amount.toString()),
        currency: payment.currency,
        status: payment.status,
        invoice: payment.invoice ? {
          invoiceNumber: payment.invoice.invoiceNumber,
          invoiceDate: payment.invoice.invoiceDate,
          dueDate: payment.invoice.dueDate,
          vendor: payment.invoice.vendor ? {
            name: payment.invoice.vendor.name,
            code: payment.invoice.vendor.code,
          } : null,
        } : null,
        cardDetails: null,
        message: 'Card details not available yet',
      };
    }

    try {
      // Decrypt card details
      const decryptedCardNumber = await decryptCardData(payment.virtualCardNumberEncrypted);
      const decryptedCvv = await decryptCardData(payment.virtualCardCvvEncrypted);

      // Log supplier access
      await this.logSupplierAccess(paymentId);

      return {
        payment: {
          id: payment.id,
          tracking_number: payment.trackingNumber,
          amount: parseFloat(payment.amount.toString()),
          currency: payment.currency,
          status: payment.status,
          payment_method: payment.paymentMethod,
          created_at: payment.createdAt?.toISOString(),
        },
        card_details: {
          card_number: decryptedCardNumber,
          cvv: decryptedCvv,
          expiry: payment.virtualCardExpiry,
          last_four: payment.virtualCardNumber?.replace(/\*/g, '').trim() || decryptedCardNumber.slice(-4),
        },
        invoice: {
          invoice_number: payment.invoice?.invoiceNumber || '',
          amount: payment.invoice ? parseFloat(payment.invoice.totalAmount.toString()) : parseFloat(payment.amount.toString()),
          due_date: payment.invoice?.dueDate ? (payment.invoice.dueDate instanceof Date ? payment.invoice.dueDate.toISOString() : payment.invoice.dueDate) : '',
          supplier_name: payment.invoice?.vendor?.name || 'Unknown Supplier',
        },
        supplierAccessCount: payment.supplierAccessCount,
        supplierLastAccessedAt: payment.supplierAccessedAt,
      };
    } catch (error) {
      console.error('Error decrypting card details:', error);
      throw new Error('Failed to decrypt card details');
    }
  }

  /**
   * Task 7.3: Get payment status
   */
  async getPaymentStatus(paymentId: string, token: string) {
    // Verify token
    if (!this.verifyToken(token, paymentId)) {
      throw new Error('Invalid or expired token');
    }

    const paymentRepository = await this.getPaymentRepository();

    const payment = await paymentRepository.findOne({
      where: { id: paymentId },
      select: ['id', 'status', 'paymentMethod', 'amount', 'currency', 'updatedAt'],
    });

    if (!payment) {
      return null;
    }

    return {
      paymentId: payment.id,
      status: payment.status,
      paymentMethod: payment.paymentMethod,
      amount: parseFloat(payment.amount.toString()),
      currency: payment.currency,
      lastUpdated: payment.updatedAt,
    };
  }

  /**
   * Log supplier access to payment
   * Update supplier_accessed_at and increment supplier_access_count
   */
  private async logSupplierAccess(paymentId: string) {
    const paymentRepository = await this.getPaymentRepository();

    await paymentRepository
      .createQueryBuilder()
      .update(Payment)
      .set({
        supplierAccessedAt: new Date(),
        supplierAccessCount: () => 'supplier_access_count + 1',
      })
      .where('id = :id', { id: paymentId })
      .execute();

    console.log(`Supplier access logged for payment: ${paymentId}`);
  }

  /**
   * Simple token verification
   * For demo purposes - in production, use JWT with expiration
   */
  private verifyToken(token: string, paymentId: string): boolean {
    try {
      const decoded = Buffer.from(token, 'base64').toString('utf-8');
      const [tokenPaymentId, , timestamp] = decoded.split(':');

      // Verify payment ID matches
      if (tokenPaymentId !== paymentId) {
        return false;
      }

      // Check if token is not older than 24 hours (for demo)
      const tokenAge = Date.now() - parseInt(timestamp);
      const maxAge = 24 * 60 * 60 * 1000; // 24 hours

      return tokenAge < maxAge;
    } catch (error) {
      console.error('Token verification error:', error);
      return false;
    }
  }
}

export const supplierService = new SupplierService();
