import { AppDataSource } from '../data-source';
import { Invoice } from '../entities/Invoice';
import { PurchaseOrder } from '../entities/PurchaseOrder';
import { GoodsReceipt } from '../entities/GoodsReceipt';
import { QCRecord } from '../entities/QCRecord';
import { logger } from '../utils/logger';

export interface ValidationResult {
  passed: boolean;
  expected?: any;
  actual?: any;
  variance?: number;
  details?: any;
}

export interface MatchResult {
  matchStatus: 'MATCHED' | 'MISMATCHED' | 'PENDING';
  paymentStatus: 'APPROVED' | 'PENDING_REVIEW' | 'ON_HOLD';
  validationFlags: string[];
  validations: {
    vendor: ValidationResult;
    product: ValidationResult;
    quantity: ValidationResult;
    price: ValidationResult;
    quality: ValidationResult;
  };
}

export class MatchValidationService {
  private invoiceRepository = AppDataSource.getRepository(Invoice);
  private poRepository = AppDataSource.getRepository(PurchaseOrder);
  private grRepository = AppDataSource.getRepository(GoodsReceipt);
  private qcRepository = AppDataSource.getRepository(QCRecord);

  /**
   * Perform 4-way match validation for an invoice
   */
  async validateInvoice(invoiceId: string): Promise<MatchResult> {
    const invoice = await this.invoiceRepository.findOne({
      where: { id: invoiceId },
      relations: ['vendor', 'purchaseOrder', 'goodsReceipt'],
    });

    if (!invoice) {
      throw new Error('Invoice not found');
    }

    // 1. Find matching PO (already linked or find by vendor + material)
    let po: PurchaseOrder | null = invoice.purchaseOrder || null;
    if (!po && invoice.purchaseOrderId) {
      po = await this.poRepository.findOne({
        where: { id: invoice.purchaseOrderId },
        relations: ['vendor'],
      });
    }

    if (!po) {
      return {
        matchStatus: 'PENDING',
        paymentStatus: 'ON_HOLD',
        validationFlags: ['MISSING_PO'],
        validations: {
          vendor: { passed: false },
          product: { passed: false },
          quantity: { passed: false },
          price: { passed: false },
          quality: { passed: false },
        },
      };
    }

    // 2. Find GR for this PO
    const gr = await this.grRepository.findOne({
      where: { purchaseOrderId: po.id },
      relations: ['vendor'],
    });

    if (!gr) {
      return {
        matchStatus: 'PENDING',
        paymentStatus: 'ON_HOLD',
        validationFlags: ['MISSING_GR'],
        validations: {
          vendor: { passed: true },
          product: { passed: true },
          quantity: { passed: false },
          price: { passed: false },
          quality: { passed: false },
        },
      };
    }

    // 3. Find QC for this PO
    const qc = await this.qcRepository.findOne({
      where: { purchaseOrderId: po.id },
      order: { testDate: 'DESC' },
    });

    if (!qc) {
      return {
        matchStatus: 'PENDING',
        paymentStatus: 'ON_HOLD',
        validationFlags: ['MISSING_QC'],
        validations: {
          vendor: { passed: true },
          product: { passed: true },
          quantity: { passed: true },
          price: { passed: true },
          quality: { passed: false },
        },
      };
    }

    // 4. Perform validations
    const validations = {
      vendor: this.validateVendor(invoice, po),
      product: this.validateProduct(invoice, gr),
      quantity: this.validateQuantity(invoice, gr),
      price: this.validatePrice(invoice, po),
      quality: this.validateQuality(qc),
    };

    // 5. Determine match status and flags
    const flags: string[] = [];
    
    if (!validations.vendor.passed) flags.push('VENDOR_MISMATCH');
    if (!validations.product.passed) flags.push('PRODUCT_MISMATCH');
    if (!validations.quantity.passed) flags.push('QUANTITY_MISMATCH');
    if (!validations.price.passed) flags.push('PRICE_MISMATCH');
    if (!validations.quality.passed) flags.push('QUALITY_FAILURE');

    const matchStatus = flags.length === 0 ? 'MATCHED' : 'MISMATCHED';
    const paymentStatus = matchStatus === 'MATCHED' ? 'APPROVED' : 'PENDING_REVIEW';

    // 6. Update invoice with match result
    await this.updateInvoiceMatchStatus(invoiceId, matchStatus, paymentStatus, flags, {
      poId: po.id,
      grId: gr.id,
      qcId: qc.id,
    });

    logger.info(`Invoice ${invoice.invoiceNumber} validation complete: ${matchStatus}`);

    return {
      matchStatus,
      paymentStatus,
      validationFlags: flags,
      validations,
    };
  }

  /**
   * Validate vendor matches between invoice and PO
   */
  private validateVendor(invoice: Invoice, po: PurchaseOrder): ValidationResult {
    const invoiceVendorId = invoice.vendorId;
    const poVendorId = po.vendorId;

    return {
      passed: invoiceVendorId === poVendorId,
      expected: poVendorId,
      actual: invoiceVendorId,
    };
  }

  /**
   * Validate product/material matches
   */
  private validateProduct(invoice: Invoice, gr: GoodsReceipt): ValidationResult {
    const invoiceMaterial = invoice.extractedData?.line_items?.[0]?.description || '';
    const grMaterial = gr.materialDescription || '';

    // Simple string comparison (could be enhanced with fuzzy matching)
    const passed = invoiceMaterial.toLowerCase().includes(grMaterial.toLowerCase()) ||
                   grMaterial.toLowerCase().includes(invoiceMaterial.toLowerCase());

    return {
      passed,
      expected: grMaterial,
      actual: invoiceMaterial,
    };
  }

  /**
   * Validate quantity with 2% tolerance
   */
  private validateQuantity(invoice: Invoice, gr: GoodsReceipt): ValidationResult {
    const tolerance = 0.02; // 2%
    const invoiceQty = invoice.extractedData?.line_items?.[0]?.quantity || 0;
    const grQty = parseFloat(gr.quantityReceived.toString());

    if (grQty === 0) {
      return { passed: false, expected: grQty, actual: invoiceQty, variance: 0 };
    }

    const diff = Math.abs(invoiceQty - grQty) / grQty;

    return {
      passed: diff <= tolerance,
      expected: grQty,
      actual: invoiceQty,
      variance: diff * 100, // percentage
    };
  }

  /**
   * Validate price with 1% tolerance
   */
  private validatePrice(invoice: Invoice, po: PurchaseOrder): ValidationResult {
    const tolerance = 0.01; // 1%
    const invoicePrice = invoice.extractedData?.line_items?.[0]?.unit_price || 0;
    const poPrice = po.items?.[0]?.unitPrice || 0;

    if (poPrice === 0) {
      return { passed: false, expected: poPrice, actual: invoicePrice, variance: 0 };
    }

    const diff = Math.abs(invoicePrice - poPrice) / poPrice;

    return {
      passed: diff <= tolerance,
      expected: poPrice,
      actual: invoicePrice,
      variance: diff * 100, // percentage
    };
  }

  /**
   * Validate quality control status
   */
  private validateQuality(qc: QCRecord): ValidationResult {
    return {
      passed: qc.overallStatus === 'PASS',
      details: {
        status: qc.overallStatus,
        measurements: qc.measurements,
      },
    };
  }

  /**
   * Update invoice with match validation results
   */
  private async updateInvoiceMatchStatus(
    invoiceId: string,
    matchStatus: string,
    paymentStatus: string,
    flags: string[],
    linkedIds: { poId: string; grId: string; qcId: string }
  ): Promise<void> {
    await this.invoiceRepository.update(invoiceId, {
      matchStatus,
      validationFlags: flags,
      purchaseOrderId: linkedIds.poId,
      goodsReceiptId: linkedIds.grId,
      matchedQCId: linkedIds.qcId,
      hasPoMatch: true,
    });
  }
}
