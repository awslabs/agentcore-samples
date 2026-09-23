import type {
  Payment,
  PaymentQueryParams,
  PaymentSummary,
  PaginatedResponse,
} from '../types/payment.types';

// Mock data for development
const MOCK_PAYMENTS: Payment[] = [
  {
    id: '1',
    invoiceId: 'inv-001',
    invoiceNumber: 'INV-2024-001',
    vendor: { id: 'v1', code: 'ACME', name: 'Acme Corp', email: 'billing@acme.com' },
    vendorId: 'v1',
    invoiceDate: '2024-01-15',
    dueDate: '2024-02-15',
    amount: 15000,
    currency: 'USD',
    paymentStatus: 'ready',
    scheduledDate: null,
    sentDate: null,
    paidDate: null,
    paymentReference: null,
    iso20022FileKey: 'payments/2024/01/payment-001.xml',
    iso20022FileMetadata: {
      fileName: 'payment-001.xml',
      fileSize: 2048,
      generatedAt: '2024-01-20T10:00:00Z',
      transactionCount: 1,
      messageId: 'MSG-001',
    },
    extractedData: {},
    createdAt: '2024-01-15T08:00:00Z',
    updatedAt: '2024-01-20T10:00:00Z',
  },
  {
    id: '2',
    invoiceId: 'inv-002',
    invoiceNumber: 'INV-2024-002',
    vendor: { id: 'v2', code: 'TECH', name: 'Tech Solutions Inc', email: 'ap@techsolutions.com' },
    vendorId: 'v2',
    invoiceDate: '2024-01-10',
    dueDate: '2024-01-25',
    amount: 8500,
    currency: 'USD',
    paymentStatus: 'sent',
    scheduledDate: '2024-01-24',
    sentDate: '2024-01-24T14:30:00Z',
    paidDate: null,
    paymentReference: 'PAY-002',
    iso20022FileKey: 'payments/2024/01/payment-002.xml',
    iso20022FileMetadata: {
      fileName: 'payment-002.xml',
      fileSize: 1856,
      generatedAt: '2024-01-24T14:00:00Z',
      transactionCount: 1,
      messageId: 'MSG-002',
    },
    extractedData: {},
    createdAt: '2024-01-10T09:00:00Z',
    updatedAt: '2024-01-24T14:30:00Z',
  },
  {
    id: '3',
    invoiceId: 'inv-003',
    invoiceNumber: 'INV-2024-003',
    vendor: { id: 'v3', code: 'SUPPLY', name: 'Office Supplies Co', email: 'invoices@officesupply.com' },
    vendorId: 'v3',
    invoiceDate: '2023-12-20',
    dueDate: '2024-01-10',
    amount: 3200,
    currency: 'USD',
    paymentStatus: 'ready',
    scheduledDate: null,
    sentDate: null,
    paidDate: null,
    paymentReference: null,
    iso20022FileKey: null,
    iso20022FileMetadata: null,
    extractedData: {},
    createdAt: '2023-12-20T11:00:00Z',
    updatedAt: '2024-01-05T16:00:00Z',
  },
];

class PaymentService {
  private baseURL = `${import.meta.env.VITE_API_BASE_URL || 'https://il9nu3s9c3.execute-api.us-east-1.amazonaws.com/prod'}/api/payments`;
  private useMockData = false; // Toggle for development

  /**
   * Get paginated list of payments with optional filters
   */
  async getPayments(
    params: PaymentQueryParams
  ): Promise<PaginatedResponse<Payment>> {
    // Use mock data for development
    if (this.useMockData) {
      await new Promise((resolve) => setTimeout(resolve, 500)); // Simulate network delay
      return {
        data: MOCK_PAYMENTS,
        pagination: {
          page: params.page,
          limit: params.limit,
          total: MOCK_PAYMENTS.length,
        },
      };
    }

    const queryString = new URLSearchParams({
      page: params.page.toString(),
      limit: params.limit.toString(),
      ...(params.status && { status: params.status.join(',') }),
      ...(params.vendorId && { vendor_id: params.vendorId }),
      ...(params.dueDateStart && { due_date_start: params.dueDateStart }),
      ...(params.dueDateEnd && { due_date_end: params.dueDateEnd }),
      ...(params.amountMin && { amount_min: params.amountMin.toString() }),
      ...(params.amountMax && { amount_max: params.amountMax.toString() }),
      ...(params.overdueOnly && { overdue_only: 'true' }),
      ...(params.search && { search: params.search }),
    }).toString();

    const response = await fetch(`${this.baseURL}?${queryString}`, {
      headers: this.getAuthHeaders(),
    });

    if (!response.ok) {
      throw new Error(`Failed to fetch payments: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Get a single payment by ID with full details
   */
  async getPaymentById(id: string): Promise<Payment> {
    const response = await fetch(`${this.baseURL}/${id}`, {
      headers: this.getAuthHeaders(),
    });

    if (!response.ok) {
      if (response.status === 404) {
        throw new Error('Payment not found');
      }
      throw new Error(`Failed to fetch payment: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Get signed S3 URL for ISO 20022 XML file download
   */
  async getISO20022FileUrl(id: string): Promise<string> {
    const response = await fetch(`${this.baseURL}/${id}/iso20022-file`, {
      headers: this.getAuthHeaders(),
    });

    if (!response.ok) {
      throw new Error(`Failed to get file URL: ${response.statusText}`);
    }

    const data = await response.json();
    return data.url;
  }

  /**
   * Get payment summary statistics
   */
  async getPaymentSummary(): Promise<PaymentSummary> {
    // Use mock data for development
    if (this.useMockData) {
      await new Promise((resolve) => setTimeout(resolve, 300)); // Simulate network delay
      return {
        totalPendingPayments: 15,
        totalPaymentAmount: 125000,
        overduePayments: 3,
        paymentsThisWeek: 8,
        paymentsByStatus: {
          ready: 5,
          scheduled: 3,
          processing: 2,
          sent: 4,
          paid: 12,
          failed: 1,
          cancelled: 0,
        },
      };
    }

    const response = await fetch(`${this.baseURL}/summary`, {
      headers: this.getAuthHeaders(),
    });

    if (!response.ok) {
      throw new Error(`Failed to fetch summary: ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * Get authentication headers with JWT token
   */
  private getAuthHeaders(): HeadersInit {
    const token = localStorage.getItem('accessToken');
    return {
      Authorization: `Bearer ${token}`,
    };
  }
}

export const paymentService = new PaymentService();
