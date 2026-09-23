import type {
  Invoice,
  InvoiceQueryParams,
  PaginatedResponse,
  InvoiceSummary,
  PaymentStatus,
  UploadResponse,
  DownloadResponse,
} from '../types/invoice.types';
import { mockInvoices, mockInvoiceSummary } from './mockInvoiceData';

// Base URL for API - Connected to deployed AWS backend
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'https://il9nu3s9c3.execute-api.us-east-1.amazonaws.com/prod';

// Use mock data for invoices until invoice backend is implemented
const USE_MOCK_DATA = false;

// Simulate API delay for development
const delay = (ms: number = 500) => new Promise((resolve) => setTimeout(resolve, ms));

class InvoiceService {
  private baseURL = `${API_BASE_URL}/api/invoices`;

  /**
   * Get authentication headers with JWT token
   */
  private getAuthHeaders(): HeadersInit {
    const token = localStorage.getItem('accessToken');
    return {
      'Authorization': token ? `Bearer ${token}` : '',
      'Content-Type': 'application/json',
    };
  }

  /**
   * Fetch invoices with filters and pagination
   */
  async getInvoices(params: InvoiceQueryParams): Promise<PaginatedResponse<Invoice>> {
    await delay(); // Simulate network delay

    // Use mock data for development
    if (USE_MOCK_DATA) {
      let filteredInvoices = [...mockInvoices];

      // Apply filters
      if (params.status && params.status.length > 0) {
        filteredInvoices = filteredInvoices.filter((inv) =>
          params.status!.includes(inv.paymentStatus)
        );
      }

      if (params.hasPoMatch !== undefined) {
        filteredInvoices = filteredInvoices.filter(
          (inv) => inv.hasPoMatch === params.hasPoMatch
        );
      }

      if (params.hasVarianceWarning) {
        filteredInvoices = filteredInvoices.filter((inv) => inv.hasVarianceWarning);
      }

      // Pagination
      const start = (params.page - 1) * params.limit;
      const end = start + params.limit;
      const paginatedData = filteredInvoices.slice(start, end);

      return {
        data: paginatedData,
        pagination: {
          page: params.page,
          limit: params.limit,
          total: filteredInvoices.length,
          totalPages: Math.ceil(filteredInvoices.length / params.limit),
        },
      };
    }

    // Real API call
    const queryString = new URLSearchParams({
      page: params.page.toString(),
      limit: params.limit.toString(),
      ...(params.status && params.status.length > 0 && { status: params.status.join(',') }),
      ...(params.vendorId && { vendor_id: params.vendorId }),
      ...(params.startDate && { start_date: params.startDate }),
      ...(params.endDate && { end_date: params.endDate }),
      ...(params.hasPoMatch !== undefined && { has_po_match: params.hasPoMatch.toString() }),
      ...(params.hasVarianceWarning && { has_variance_warning: 'true' }),
    }).toString();

    try {
      const response = await fetch(`${this.baseURL}?${queryString}`, {
        headers: this.getAuthHeaders(),
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch invoices: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Error fetching invoices:', error);
      throw error;
    }
  }

  /**
   * Fetch a single invoice by ID
   */
  async getInvoiceById(id: string): Promise<Invoice> {
    await delay();

    try {
      const response = await fetch(`${this.baseURL}/${id}`, {
        headers: this.getAuthHeaders(),
      });

      if (!response.ok) {
        if (response.status === 404) {
          throw new Error('Invoice not found');
        }
        throw new Error(`Failed to fetch invoice: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Error fetching invoice:', error);
      throw error;
    }
  }

  /**
   * Update invoice payment status
   */
  async updateInvoiceStatus(id: string, status: PaymentStatus): Promise<Invoice> {
    await delay();

    try {
      const response = await fetch(`${this.baseURL}/${id}`, {
        method: 'PATCH',
        headers: this.getAuthHeaders(),
        body: JSON.stringify({ payment_status: status }),
      });

      if (!response.ok) {
        throw new Error(`Failed to update invoice: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Error updating invoice status:', error);
      throw error;
    }
  }

  /**
   * Link invoice to a purchase order
   */
  async linkToPurchaseOrder(invoiceId: string, poId: string): Promise<Invoice> {
    await delay();

    try {
      const response = await fetch(`${this.baseURL}/${invoiceId}/link-po`, {
        method: 'POST',
        headers: this.getAuthHeaders(),
        body: JSON.stringify({ poId }),
      });

      if (!response.ok) {
        throw new Error(`Failed to link invoice to PO: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Error linking invoice to PO:', error);
      throw error;
    }
  }

  /**
   * Get signed URL for ISO 20022 XML file
   */
  async getISO20022FileUrl(id: string): Promise<string> {
    await delay();

    try {
      const response = await fetch(`${this.baseURL}/${id}/iso20022-file`, {
        headers: this.getAuthHeaders(),
      });

      if (!response.ok) {
        throw new Error(`Failed to get file URL: ${response.statusText}`);
      }

      const data = await response.json();
      return data.url;
    } catch (error) {
      console.error('Error getting ISO 20022 file URL:', error);
      throw error;
    }
  }

  /**
   * Get invoice statistics for summary cards
   */
  async getStatistics(): Promise<InvoiceSummary> {
    await delay();

    // Use mock data for development
    if (USE_MOCK_DATA) {
      return mockInvoiceSummary;
    }

    try {
      const response = await fetch(`${this.baseURL}/stats`, {
        headers: this.getAuthHeaders(),
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch statistics: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Error fetching statistics:', error);
      throw error;
    }
  }

  /**
   * Upload invoice file (PDF, PNG, or JPG)
   */
  async uploadInvoice(file: File, _onProgress?: (progress: number) => void): Promise<UploadResponse> {
    try {
      const formData = new FormData();
      formData.append('file', file);

      const token = localStorage.getItem('accessToken');
      const headers: HeadersInit = {};
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const response = await fetch(`${this.baseURL}/upload`, {
        method: 'POST',
        headers,
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ message: 'Upload failed' }));
        throw new Error(errorData.message || `HTTP error! status: ${response.status}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Error uploading invoice:', error);
      throw error;
    }
  }

  /**
   * Download original invoice file
   */
  async downloadInvoice(invoiceId: string): Promise<DownloadResponse> {
    try {
      const response = await fetch(`${this.baseURL}/${invoiceId}/download`, {
        headers: this.getAuthHeaders(),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ message: 'Download failed' }));
        throw new Error(errorData.message || `HTTP error! status: ${response.status}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Error downloading invoice:', error);
      throw error;
    }
  }
}

// Export singleton instance
export const invoiceService = new InvoiceService();
