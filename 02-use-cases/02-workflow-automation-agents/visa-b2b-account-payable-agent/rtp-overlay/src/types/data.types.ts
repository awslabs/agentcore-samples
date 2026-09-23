// Vendor
export interface Vendor {
  id: string;
  code: string;
  name: string;
  performanceScore: number;
  onTimeDeliveryRate: number;
  qualityScore: number;
  invoiceAccuracy: number;
}

// Purchase Order
export interface PurchaseOrderItem {
  id: string;
  description: string;
  quantity: number;
  receivedQuantity: number; // NEW: Track received quantity per item
  unitPrice: number;
  totalPrice: number;
}

export type POStatus = 
  | 'active' 
  | 'partially-received' 
  | 'received' 
  | 'invoiced' 
  | 'closed' 
  | 'exception';

export type MatchStatus = 'no-docs' | '2-way' | '3-way' | 'exception';

export interface POAttachments {
  poDocuments: number;
  receipts: number;
  invoices: number;
  total: number;
}

export interface PurchaseOrder {
  id: string;
  poNumber: string;
  vendor: Vendor;
  sourceLocation: string;
  assignedDate: Date;
  dueDate: Date; // NEW: Expected delivery date
  warehouse?: string; // NEW: Optional destination warehouse
  status: POStatus; // ENHANCED: More status options
  items: PurchaseOrderItem[];
  totalAmount: number;
  currency: string; // NEW: Currency code (default USD)
  
  // NEW: Fulfillment tracking
  orderedQuantity: number; // Total ordered across all items
  receivedQuantity: number; // Total received across all items
  fulfillmentPercentage: number; // Calculated: (received / ordered) * 100
  hasPartialReceipt: boolean; // True if 0 < received < ordered
  
  // NEW: Financial tracking
  remainingAmount: number; // Amount not yet invoiced
  
  // NEW: Matching status
  matchStatus: MatchStatus;
  hasReceipt: boolean;
  hasInvoice: boolean;
  variancePercentage: number; // Variance between PO and invoice amounts
  
  // NEW: Attachments
  attachments: POAttachments;
  
  // NEW: Computed fields
  isOverdue: boolean; // True if dueDate < today and not received
}

// Filter interface for PO list
export interface POFilters {
  status: POStatus[];
  exceptionsOnly: boolean;
  vendorId: string | null;
  warehouseId: string | null;
  dateRange: {
    start: string | null;
    end: string | null;
  };
  overdueOnly: boolean;
  unmatchedInvoicesOnly: boolean;
}

// Goods Receipt
export type GoodsReceiptStatus = 'pending_qc' | 'qc_passed' | 'qc_failed';

export interface GoodsReceipt {
  id: string;
  purchaseOrderId: string;
  purchaseOrder?: {
    id: string;
    poNumber: string;
    vendor: Vendor;
  };
  deliveryDate: Date;
  quantityReceived: number;
  conditionNotes: string;
  deliveryReference: string;
  receivedBy: string;
  status: GoodsReceiptStatus;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface GoodsReceiptData {
  deliveryDate: Date;
  quantityReceived: number;
  conditionNotes: string;
  deliveryReference: string;
}

// Delivery
export interface Delivery {
  id: string;
  goodsReceiptId: string;
  purchaseOrderId: string;
  vendor: Vendor;
  deliveryDate: Date;
  status: 'awaiting_qc' | 'qc_in_progress' | 'qc_completed';
}

// Quality Inspection
export interface QualityCharacteristic {
  id: string;
  name: string;
  expectedValue: string;
  inputType: 'text' | 'number' | 'select';
  options?: string[];
}

export interface QualityCharacteristicResult {
  characteristicId: string;
  name: string;
  expectedValue: string;
  actualValue: string;
  result: 'pass' | 'fail';
}

export interface QualityInspection {
  id: string;
  deliveryId: string;
  inspectionDate: Date;
  inspector: string;
  characteristics: QualityCharacteristicResult[];
  overallResult: 'pass' | 'fail';
  notes: string;
}

export interface QCData {
  characteristics: QualityCharacteristicResult[];
  notes: string;
}

// Invoice
export interface Invoice {
  id: string;
  invoiceNumber: string;
  vendor: Vendor;
  purchaseOrderId: string;
  invoiceDate: Date;
  dueDate: Date;
  amount: number;
  status: 'pending_review' | 'approved' | 'disputed' | 'paid';
  mismatchType?: 'quantity' | 'price' | 'quality' | 'multiple';
}

export interface MismatchDetail {
  field: string;
  poValue: string | number;
  grValue: string | number;
  qcValue: string | number;
  invoiceValue: string | number;
  variance: number;
}

export interface Adjustment {
  field: string;
  newValue: string | number;
  reason: string;
}

// Payment
export interface Payment {
  id: string;
  invoiceId: string;
  vendor: Vendor;
  amount: number;
  scheduledDate: Date;
  paymentMethod: string;
  status: 'scheduled' | 'processing' | 'completed' | 'failed';
}
