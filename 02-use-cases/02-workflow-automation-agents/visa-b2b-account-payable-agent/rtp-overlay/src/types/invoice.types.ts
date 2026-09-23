// Invoice Management Types

export type PaymentStatus = 
  | 'pending' 
  | 'processing' 
  | 'generated' 
  | 'sent' 
  | 'paid' 
  | 'failed';

export interface ExtractedInvoiceData {
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
    po_reference?: string;
    line_items: LineItem[];
    subtotal: number;
    tax_amount: number;
    total: number;
  };
  payment: {
    bank_name: string;
    account_name: string;
    account_number: string;
    routing_aba: string;
    swift_bic?: string;
    iban?: string;
  };
}

export interface LineItem {
  description: string;
  quantity: number;
  unit_price: number;
  amount: number;
}

export interface InvoiceVendor {
  id: string;
  code: string;
  name: string;
  email?: string;
  phone?: string;
  address?: string;
}

export interface InvoicePurchaseOrder {
  id: string;
  poNumber: string;
  vendor: InvoiceVendor;
  totalAmount: number;
  status: string;
  assignedDate: string;
  sourceLocation: string;
}

export interface InvoiceGoodsReceipt {
  id: string;
  deliveryReference: string;
  deliveryDate: string;
  quantityReceived: number;
  conditionNotes?: string;
  receivedBy: {
    id: string;
    name: string;
    email: string;
  };
  documents: ReceiptDocument[];
}

export interface ReceiptDocument {
  id: string;
  fileName: string;
  fileType: string;
  fileSize: number;
  uploadedAt: string;
}

export interface Invoice {
  id: string;
  invoiceNumber: string;
  invoiceDate: string;
  dueDate?: string;
  vendor?: InvoiceVendor;
  vendorId?: string;
  purchaseOrder?: InvoicePurchaseOrder;
  purchaseOrderId?: string;
  goodsReceipt?: InvoiceGoodsReceipt;
  goodsReceiptId?: string;
  currency: string;
  subtotal: number;
  taxAmount: number;
  totalAmount: number;
  paymentStatus: PaymentStatus;
  iso20022FileKey?: string;
  extractedData: ExtractedInvoiceData;
  variancePercentage?: number;
  hasPoMatch: boolean;
  hasVarianceWarning: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface InvoiceFilters {
  status: PaymentStatus[];
  vendorId: string | null;
  startDate: string | null;
  endDate: string | null;
  poMatchStatus: 'all' | 'matched' | 'unmatched' | 'variance';
}

export interface PaginationState {
  page: number;
  limit: number;
  total: number;
  totalPages?: number;
}

export interface InvoiceSummary {
  totalInvoices: number;
  totalAmount: number;
  pendingPayments: number;
  overdueInvoices: number;
}

export interface PaginatedResponse<T> {
  data: T[];
  pagination: PaginationState;
}

export interface InvoiceQueryParams {
  page: number;
  limit: number;
  status?: PaymentStatus[];
  vendorId?: string;
  startDate?: string;
  endDate?: string;
  hasPoMatch?: boolean;
  hasVarianceWarning?: boolean;
}

export interface UploadResponse {
  message: string;
  fileKey: string;
  fileName: string;
  fileSize: number;
}

export interface DownloadResponse {
  url: string;
  fileName: string;
  expiresIn: number;
}
