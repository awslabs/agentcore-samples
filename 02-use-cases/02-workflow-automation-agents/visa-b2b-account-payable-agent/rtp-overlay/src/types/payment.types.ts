// Payment Management Types

export type PaymentStatus = 
  | 'ready'        // Invoice approved, ready for payment
  | 'scheduled'    // Payment scheduled for future date
  | 'processing'   // Payment being processed
  | 'sent'         // Payment file sent to bank
  | 'paid'         // Payment completed
  | 'failed'       // Payment failed
  | 'cancelled';   // Payment cancelled

export interface ISO20022FileMetadata {
  fileName: string;
  fileSize: number;
  generatedAt: string;
  transactionCount: number;
  messageId: string;
}

export interface Payment {
  id: string;
  invoiceId: string;
  invoiceNumber: string;
  vendor: {
    id: string;
    code: string;
    name: string;
    email?: string;
    phone?: string;
  };
  vendorId: string;
  invoiceDate: string; // ISO date
  dueDate: string;
  amount: number;
  currency: string;
  paymentStatus: PaymentStatus;
  scheduledDate: string | null;
  sentDate: string | null;
  paidDate: string | null;
  paymentReference: string | null;
  iso20022FileKey: string | null;
  iso20022FileMetadata: ISO20022FileMetadata | null;
  extractedData: any; // Invoice extracted data from AI
  createdAt: string;
  updatedAt: string;
}

export interface PaymentHistory {
  id: string;
  paymentId: string;
  status: PaymentStatus;
  previousStatus: PaymentStatus | null;
  performedBy: string | null; // User ID or null for system
  performedByName: string | null;
  timestamp: Date;
  notes: string | null;
  metadata: any; // Additional context
}

export interface PaymentSummary {
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

export interface PaymentFilters {
  status: PaymentStatus[];
  vendorId: string | null;
  dueDateRange: {
    start: string | null;
    end: string | null;
  };
  amountRange: {
    min: number | null;
    max: number | null;
  };
  overdueOnly: boolean;
  search: string;
}

export interface PaginationState {
  page: number;
  limit: number;
  total: number;
}

export interface PaginatedResponse<T> {
  data: T[];
  pagination: PaginationState;
}

export interface PaymentQueryParams {
  page: number;
  limit: number;
  status?: PaymentStatus[];
  vendorId?: string;
  dueDateStart?: string;
  dueDateEnd?: string;
  amountMin?: number;
  amountMax?: number;
  overdueOnly?: boolean;
  search?: string;
}
