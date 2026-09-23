import { Invoice, PaymentStatus, MatchStatus } from '../entities/Invoice';
import { InvoiceRepository, InvoiceFilters, PaginatedResult } from '../repositories/InvoiceRepository';
import { getDataSource } from '../config/database';
import { Vendor } from '../entities/Vendor';
import { PurchaseOrder } from '../entities/PurchaseOrder';
import { GoodsReceipt } from '../entities/GoodsReceipt';
import { AppError } from '../middleware/errorHandler';
import { DocumentJobService } from './documentJobService';

export interface CreateInvoiceDto {
  jobId?: string; // Optional job ID for tracking
  extractedData: {
    supplier: {
      name: string;
      address_lines: string[];
      email?: string;
      phone?: string;
    };
    invoice: {
      number: string;
      date: string;
      due_date?: string;
      currency: string;
      line_items: Array<{
        description: string;
        quantity: number;
        unit_price: number;
        amount: number;
      }>;
      subtotal: number;
      tax_amount: number;
      total: number;
    };
    payment?: {
      bank_name: string;
      account_name: string;
      account_number: string;
      routing_aba: string;
      swift_bic?: string;
      iban?: string;
    };
    bill_to?: {
      name: string;
      address_lines: string[];
    };
  };
  sourceFileKey: string;
  iso20022FileKey?: string;
}

export interface MatchResult {
  matched: boolean;
  poId?: string;
  grId?: string;
  variance?: number;
  hasWarning?: boolean;
  reason?: string;
  threeWayMatch?: boolean;
}

export class InvoiceService {
  private invoiceRepo: InvoiceRepository;
  private documentJobService: DocumentJobService;

  constructor() {
    this.invoiceRepo = new InvoiceRepository();
    this.documentJobService = new DocumentJobService();
  }

  async createFromExtractedData(data: CreateInvoiceDto): Promise<Invoice> {
    const startTime = Date.now();

    try {
      // 1. Find or create vendor
      const vendor = await this.findOrCreateVendor(data.extractedData.supplier);

      // 2. Check for duplicate invoice number - allow re-upload (update existing)
      const existing = await this.invoiceRepo.findByInvoiceNumber(
        data.extractedData.invoice.number
      );
      
      let invoice: Invoice;
      
      if (existing) {
        console.log(`Invoice ${data.extractedData.invoice.number} already exists - updating with new data`);
        
        // Update existing invoice with new data
        invoice = await this.invoiceRepo.update(existing.id, {
          invoiceDate: new Date(data.extractedData.invoice.date),
          dueDate: data.extractedData.invoice.due_date
            ? new Date(data.extractedData.invoice.due_date)
            : undefined,
          vendorId: vendor.id,
          currency: data.extractedData.invoice.currency || 'USD',
          subtotal: data.extractedData.invoice.subtotal,
          taxAmount: data.extractedData.invoice.tax_amount,
          totalAmount: data.extractedData.invoice.total,
          paymentStatus: data.iso20022FileKey ? PaymentStatus.GENERATED : PaymentStatus.PENDING,
          extractedData: data.extractedData,
          sourceFileKey: data.sourceFileKey,
          iso20022FileKey: data.iso20022FileKey,
        });
        
      } else {
        // 3. Create new invoice
        invoice = await this.invoiceRepo.create({
        invoiceNumber: data.extractedData.invoice.number,
        invoiceDate: new Date(data.extractedData.invoice.date),
        dueDate: data.extractedData.invoice.due_date
          ? new Date(data.extractedData.invoice.due_date)
          : undefined,
        vendorId: vendor.id,
        currency: data.extractedData.invoice.currency || 'USD',
        subtotal: data.extractedData.invoice.subtotal,
        taxAmount: data.extractedData.invoice.tax_amount,
        totalAmount: data.extractedData.invoice.total,
        // Set status to 'generated' if ISO20022 file was created, otherwise 'pending'
        paymentStatus: data.iso20022FileKey ? PaymentStatus.GENERATED : PaymentStatus.PENDING,
        extractedData: data.extractedData,
        sourceFileKey: data.sourceFileKey,
        iso20022FileKey: data.iso20022FileKey,
        hasPoMatch: false,
        hasVarianceWarning: false,
        });
      }

      // 4. CRITICAL: Ensure extractedData is available for validation
      // TypeORM may not always populate JSONB fields correctly after create/update
      invoice.extractedData = data.extractedData;

      // 5. Attempt PO and GR matching (3-way match)
      await this.performThreeWayMatch(invoice);

      // 5. Update document job if provided
      if (data.jobId) {
        const processingTime = Date.now() - startTime;
        await this.documentJobService.markCompleted(
          data.jobId,
          invoice.id,
          processingTime
        );
      }

      // 6. Return updated invoice with relations
      const result = await this.invoiceRepo.findById(invoice.id);
      if (!result) {
        throw new AppError('Invoice not found after creation', 500);
      }

      return result;
    } catch (error) {
      // Mark job as failed if provided
      if (data.jobId) {
        await this.documentJobService.markFailed(
          data.jobId,
          error instanceof Error ? error.message : 'Unknown error'
        );
      }
      throw error;
    }
  }

  async performThreeWayMatch(invoice: Invoice): Promise<MatchResult> {
    // 1. Extract PO reference from invoice data
    const poReference = this.extractPoReference(invoice.extractedData);

    if (!poReference) {
      console.log('No PO reference found in invoice');
      await this.invoiceRepo.update(invoice.id, {
        matchStatus: MatchStatus.PENDING,
        validationFlags: ['no_po_reference'],
      });
      return { matched: false, reason: 'no_po_reference' };
    }

    // 2. Find PO by number
    const dataSource = await getDataSource();
    const poRepo = dataSource.getRepository(PurchaseOrder);
    const grRepo = dataSource.getRepository(GoodsReceipt);

    const po = await poRepo.findOne({
      where: { poNumber: poReference },
      relations: ['vendor', 'items'],
    });

    if (!po) {
      console.log(`PO not found: ${poReference}`);
      await this.invoiceRepo.update(invoice.id, {
        matchStatus: MatchStatus.PENDING,
        validationFlags: ['po_not_found'],
      });
      return { matched: false, reason: 'po_not_found' };
    }

    // 3. Verify vendor match
    if (invoice.vendorId && invoice.vendorId !== po.vendorId) {
      console.warn(
        `Vendor mismatch: Invoice vendor ${invoice.vendorId} != PO vendor ${po.vendorId}`
      );
      await this.invoiceRepo.update(invoice.id, {
        purchaseOrderId: po.id,
        matchStatus: MatchStatus.MISMATCHED,
        validationFlags: ['vendor_mismatch'],
      });
      return { matched: false, reason: 'vendor_mismatch', poId: po.id };
    }

    // 4. Find GR linked to this PO
    const gr = await grRepo.findOne({
      where: { purchaseOrderId: po.id },
      order: { createdAt: 'DESC' }, // Get most recent GR if multiple exist
    });

    if (!gr) {
      console.log(`No GR found for PO: ${po.poNumber}`);
      // PO matched but no GR - 2-way match only
      const variance = this.calculateVariance(
        Number(invoice.totalAmount),
        Number(po.totalAmount)
      );

      await this.invoiceRepo.update(invoice.id, {
        purchaseOrderId: po.id,
        hasPoMatch: true,
        variancePercentage: variance,
        hasVarianceWarning: Math.abs(variance) > 10,
        matchStatus: MatchStatus.PENDING,
        validationFlags: ['no_gr_found', ...(Math.abs(variance) > 10 ? ['high_variance'] : [])],
      });

      return {
        matched: true,
        poId: po.id,
        variance,
        hasWarning: Math.abs(variance) > 10,
        threeWayMatch: false,
        reason: 'no_gr_found',
      };
    }

    // 5. Calculate variance (PO vs Invoice)
    const variance = this.calculateVariance(
      Number(invoice.totalAmount),
      Number(po.totalAmount)
    );

    // 6. Validate quantities (3-way match)
    const quantityMatch = this.validateQuantities(invoice, po, gr);

    // 7. Determine match status
    const hasHighVariance = Math.abs(variance) > 10;
    const validationFlags: string[] = [];

    if (hasHighVariance) {
      validationFlags.push('high_variance');
    }
    if (!quantityMatch) {
      validationFlags.push('quantity_mismatch');
    }

    const matchStatus =
      !hasHighVariance && quantityMatch ? MatchStatus.MATCHED : MatchStatus.MISMATCHED;

    // 8. Update invoice with 3-way match results
    await this.invoiceRepo.update(invoice.id, {
      purchaseOrderId: po.id,
      goodsReceiptId: gr.id,
      hasPoMatch: true,
      variancePercentage: variance,
      hasVarianceWarning: hasHighVariance || !quantityMatch,
      matchStatus,
      validationFlags: validationFlags.length > 0 ? validationFlags : [],
    });

    console.log(
      `3-way match completed for invoice ${invoice.invoiceNumber}: PO=${po.poNumber}, GR=${gr.id}, Status=${matchStatus}`
    );

    return {
      matched: true,
      poId: po.id,
      grId: gr.id,
      variance,
      hasWarning: hasHighVariance || !quantityMatch,
      threeWayMatch: true,
    };
  }

  private validateQuantities(
    invoice: Invoice,
    po: PurchaseOrder,
    gr: GoodsReceipt
  ): boolean {
    // DEFENSIVE: Handle both extractedData structures for backward compatibility
    // New structure: extractedData.invoice.line_items (after Python fix)
    // Old structure: extractedData.line_items (existing data in DB)
    const lineItems = invoice.extractedData?.invoice?.line_items || invoice.extractedData?.line_items;

    // Extract total quantity from invoice line items
    const invoiceQty = lineItems?.reduce(
      (sum: number, item: any) => sum + (Number(item.quantity) || 0),
      0
    ) || 0;

    // Get PO total quantity - DEFENSIVE: check if items exist
    if (!po.items || po.items.length === 0) {
      console.error('[InvoiceService] ERROR: PO items not loaded or empty!');
      console.error('[InvoiceService] PO object keys:', Object.keys(po));
      console.error('[InvoiceService] PO.items value:', po.items);
      return false;
    }
    const poQty = po.items.reduce((sum, item) => sum + Number(item.quantity), 0);

    // Get GR quantity
    const grQty = Number(gr.quantityReceived) || 0;

    // Allow 5% tolerance for quantity matching
    const tolerance = 0.05;

    const poGrDiff = Math.abs(poQty - grQty);
    const poGrTolerance = poQty * tolerance;
    const poGrMatch = poGrDiff <= poGrTolerance;

    const grInvoiceDiff = Math.abs(grQty - invoiceQty);
    const grInvoiceTolerance = grQty * tolerance;
    const grInvoiceMatch = grInvoiceDiff <= grInvoiceTolerance;

    console.log(
      `[InvoiceService] Quantity validation:`,
      `Invoice=${invoiceQty}, PO=${poQty} (from ${po.items.length} items), GR=${grQty}`,
      `PO-GR: diff=${poGrDiff.toFixed(2)}, tolerance=${poGrTolerance.toFixed(2)}, match=${poGrMatch}`,
      `GR-Invoice: diff=${grInvoiceDiff.toFixed(2)}, tolerance=${grInvoiceTolerance.toFixed(2)}, match=${grInvoiceMatch}`,
      `Overall Match=${poGrMatch && grInvoiceMatch}`
    );

    return poGrMatch && grInvoiceMatch;
  }

  async matchToPurchaseOrder(invoice: Invoice): Promise<MatchResult> {
    // Deprecated: Use performThreeWayMatch instead
    // Kept for backward compatibility
    return this.performThreeWayMatch(invoice);
  }

  calculateVariance(invoiceAmount: number, poAmount: number): number {
    if (poAmount === 0) return 0;
    return ((invoiceAmount - poAmount) / poAmount) * 100;
  }

  private extractPoReference(extractedData: any): string | null {
    // Try to find PO reference in various places
    const invoice = extractedData?.invoice;

    if (!invoice) return null;

    // Check for explicit PO reference field
    if (invoice.po_reference) {
      return invoice.po_reference;
    }

    // Check invoice number for PO pattern (e.g., "INV-PO_2025_00001" or "INV-PO-2025-00001")
    if (invoice.number) {
      // Match patterns like "PO_2025_00001" or "PO-2025-00001"
      const match = invoice.number.match(/PO[_-]\d{4}[_-]\d{5}/i);
      if (match) {
        // Normalize to standard format: PO-YYYY-NNNNN
        return match[0].replace(/_/g, '-');
      }
    }

    // Check line items for PO references
    if (invoice.line_items && Array.isArray(invoice.line_items)) {
      for (const item of invoice.line_items) {
        if (item.po_number) {
          return item.po_number;
        }
        // Look for PO pattern in description (e.g., "PO-2024-0001")
        const match = item.description?.match(/PO[_-]\d{4}[_-]\d{4,5}/i);
        if (match) {
          // Normalize to standard format
          return match[0].replace(/_/g, '-');
        }
      }
    }

    return null;
  }

  private async findOrCreateVendor(supplierData: any): Promise<Vendor> {
    const dataSource = await getDataSource();
    const vendorRepo = dataSource.getRepository(Vendor);

    // Strip vendor codes in parentheses: "Global Parts Ltd (GLOBAL003)" → "Global Parts Ltd"
    const lastParenIndex = supplierData.name.lastIndexOf('(');
    const cleanName = lastParenIndex >= 0 
      ? supplierData.name.substring(0, lastParenIndex).trim()
      : supplierData.name.trim();

    // Try exact match first with cleaned name
    let existing = await vendorRepo.findOne({
      where: { name: cleanName },
    });

    // Try fuzzy match if exact fails
    if (!existing) {
      const allVendors = await vendorRepo.find();
      existing = allVendors.find(v => this.fuzzyMatch(v.name, cleanName, 0.85)) || null;
      
      if (existing) {
        console.log(`Fuzzy matched vendor: "${cleanName}" → "${existing.name}"`);
      }
    }

    if (existing) {
      return existing;
    }

    // Create new vendor with cleaned name
    console.log(`Creating new vendor: "${cleanName}"`);
    const vendor = vendorRepo.create({
      name: cleanName,
      code: this.generateVendorCode(cleanName),
      email: supplierData.email || '',
      phone: supplierData.phone || '',
      address: supplierData.address_lines?.join(', ') || '',
      isActive: true,
    });

    return vendorRepo.save(vendor);
  }

  private fuzzyMatch(str1: string, str2: string, threshold: number): boolean {
    // Normalize strings: lowercase, remove extra spaces
    const s1 = str1.toLowerCase().replace(/\s+/g, ' ').trim();
    const s2 = str2.toLowerCase().replace(/\s+/g, ' ').trim();

    // Exact match after normalization
    if (s1 === s2) return true;

    // Calculate Levenshtein distance
    const distance = this.levenshteinDistance(s1, s2);
    const maxLength = Math.max(s1.length, s2.length);
    const similarity = 1 - distance / maxLength;

    return similarity >= threshold;
  }

  private levenshteinDistance(str1: string, str2: string): number {
    // Limit string length to prevent DoS attacks
    const MAX_LENGTH = 1000;
    if (str1.length > MAX_LENGTH || str2.length > MAX_LENGTH) {
      // Return max distance for strings that are too long
      return Math.max(str1.length, str2.length);
    }
  
    const matrix: number[][] = [];

    for (let i = 0; i <= str2.length; i++) {
      matrix[i] = [i];
    }

    for (let j = 0; j <= str1.length; j++) {
      matrix[0][j] = j;
    }

    for (let i = 1; i <= str2.length; i++) {
      for (let j = 1; j <= str1.length; j++) {
        if (str2.charAt(i - 1) === str1.charAt(j - 1)) {
          matrix[i][j] = matrix[i - 1][j - 1];
        } else {
          matrix[i][j] = Math.min(
            matrix[i - 1][j - 1] + 1, // substitution
            matrix[i][j - 1] + 1,     // insertion
            matrix[i - 1][j] + 1      // deletion
          );
        }
      }
    }

    return matrix[str2.length][str1.length];
  }

  private generateVendorCode(name: string): string {
    // Generate vendor code from name (first 6 chars uppercase + random 3 digits)
    const prefix = name
      .replace(/[^a-zA-Z]/g, '')
      .substring(0, 6)
      .toUpperCase()
      .padEnd(6, 'X');
    const suffix = Math.floor(Math.random() * 1000)
      .toString()
      .padStart(3, '0');
    return `${prefix}${suffix}`;
  }

  async findAll(filters: InvoiceFilters): Promise<PaginatedResult<Invoice>> {
    return this.invoiceRepo.findAll(filters);
  }

  async findById(id: string): Promise<Invoice> {
    const invoice = await this.invoiceRepo.findById(id);
    if (!invoice) {
      throw new AppError('Invoice not found', 404);
    }
    return invoice;
  }

  async updatePaymentStatus(id: string, status: PaymentStatus): Promise<Invoice> {
    const invoice = await this.findById(id);

    // Validate status transition
    this.validateStatusTransition(invoice.paymentStatus, status);

    return this.invoiceRepo.update(id, { paymentStatus: status });
  }

  private validateStatusTransition(current: PaymentStatus, next: PaymentStatus): void {
    const validTransitions: Record<PaymentStatus, PaymentStatus[]> = {
      [PaymentStatus.PENDING]: [
        PaymentStatus.PROCESSING,
        PaymentStatus.CANCELLED,
      ],
      [PaymentStatus.PROCESSING]: [
        PaymentStatus.GENERATED,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
      ],
      [PaymentStatus.GENERATED]: [
        PaymentStatus.SENT,
        PaymentStatus.FAILED,
      ],
      [PaymentStatus.SENT]: [
        PaymentStatus.PAID,
        PaymentStatus.FAILED,
      ],
      [PaymentStatus.PAID]: [],
      [PaymentStatus.FAILED]: [
        PaymentStatus.PENDING,
        PaymentStatus.CANCELLED,
      ],
      [PaymentStatus.CANCELLED]: [],
    };

    if (!validTransitions[current]?.includes(next)) {
      throw new AppError(
        `Invalid status transition from ${current} to ${next}`,
        400
      );
    }
  }

  async linkToPurchaseOrder(invoiceId: string, poId: string): Promise<Invoice> {
    const invoice = await this.findById(invoiceId);

    const dataSource = await getDataSource();
    const poRepo = dataSource.getRepository(PurchaseOrder);
    const po = await poRepo.findOne({ where: { id: poId } });

    if (!po) {
      throw new AppError('Purchase order not found', 404);
    }

    // Verify vendor match
    if (invoice.vendorId !== po.vendorId) {
      throw new AppError('Vendor mismatch between invoice and PO', 400);
    }

    // Calculate variance
    const variance = this.calculateVariance(
      Number(invoice.totalAmount),
      Number(po.totalAmount)
    );

    return this.invoiceRepo.update(invoiceId, {
      purchaseOrderId: poId,
      hasPoMatch: true,
      variancePercentage: variance,
      hasVarianceWarning: Math.abs(variance) > 10,
    });
  }

  async getStatistics(): Promise<any> {
    return this.invoiceRepo.getStatistics();
  }

  async canProcessPayment(invoiceId: string): Promise<{
    canPay: boolean;
    reason?: string;
    warnings?: string[];
  }> {
    const invoice = await this.findById(invoiceId);

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
    if (invoice.matchStatus === MatchStatus.MISMATCHED) {
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
