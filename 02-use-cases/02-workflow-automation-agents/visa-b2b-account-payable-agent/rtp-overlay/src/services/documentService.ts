/**
 * Document Service - Handles invoice and goods receipt document uploads with AI extraction
 * 
 * This service manages:
 * - File uploads to S3
 * - Job status polling for AI extraction
 * - Retrieving extraction results
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'https://56hm3anw06.execute-api.us-east-1.amazonaws.com/prod';

// Get auth headers with JWT token
const getAuthHeaders = (): HeadersInit => {
  const token = localStorage.getItem('accessToken');
  return {
    'Authorization': token ? `Bearer ${token}` : '',
    'Content-Type': 'application/json',
  };
};

// Types
export interface UploadInvoiceResponse {
  message: string;
  jobId: string;
  fileKey: string;
}

export interface UploadGRResponse {
  message: string;
  jobId: string;
  fileKey: string;
}

export interface JobStatus {
  id: string;
  jobType: 'invoice' | 'goods_receipt';
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  resultId?: string;
  errorMessage?: string;
  processingTimeMs?: number;
}

export interface Invoice {
  id: string;
  invoiceNumber: string;
  invoiceDate: string;
  dueDate: string;
  vendor: {
    id: string;
    name: string;
  };
  purchaseOrder?: {
    id: string;
    poNumber: string;
  };
  currency: string;
  subtotal: number;
  taxAmount: number;
  totalAmount: number;
  paymentStatus: string;
  iso20022FileKey?: string;
  extractedData: any;
  sourceFileKey: string;
  variancePercentage?: number;
  hasPoMatch: boolean;
  hasVarianceWarning: boolean;
}

export interface GoodsReceipt {
  id: string;
  grNumber: string;
  purchaseOrder?: {
    id: string;
    poNumber: string;
  };
  vendor: {
    id: string;
    name: string;
  };
  receivedDate: string;
  receivedBy?: string;
  receivedQuantity: number;
  unit?: string;
  materialDescription?: string;
  bolNumber?: string;
  deliveryLocation?: string;
  notes?: string;
  sourceFileKey?: string;
  extractedData?: any;
  confidenceScores?: any;
}

class DocumentService {
  /**
   * Upload invoice file for AI extraction
   */
  async uploadInvoice(file: File): Promise<UploadInvoiceResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const token = localStorage.getItem('accessToken');
    const headers: HeadersInit = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE_URL}/api/invoices/upload`, {
      method: 'POST',
      headers,
      body: formData,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ message: 'Upload failed' }));
      throw new Error(errorData.message || `Upload failed: ${response.statusText}`);
    }

    return await response.json();
  }

  /**
   * Upload goods receipt document for AI extraction
   */
  async uploadGoodsReceipt(file: File): Promise<UploadGRResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const token = localStorage.getItem('accessToken');
    const headers: HeadersInit = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE_URL}/api/goods-receipts/upload`, {
      method: 'POST',
      headers,
      body: formData,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ message: 'Upload failed' }));
      throw new Error(errorData.message || `Upload failed: ${response.statusText}`);
    }

    return await response.json();
  }

  /**
   * Get job status for extraction
   */
  async getJobStatus(jobId: string): Promise<JobStatus> {
    const response = await fetch(`${API_BASE_URL}/api/document-jobs/${jobId}`, {
      headers: getAuthHeaders(),
    });

    if (!response.ok) {
      throw new Error(`Failed to get job status: ${response.statusText}`);
    }

    return await response.json();
  }

  /**
   * Poll for job completion with 2-second intervals
   * Returns when job is COMPLETED or FAILED
   * Throws error if job fails or times out
   */
  async pollForJobCompletion(
    jobId: string,
    maxAttempts: number = 30, // 30 attempts * 2 seconds = 60 seconds max
    intervalMs: number = 2000
  ): Promise<JobStatus> {
    let attempts = 0;

    while (attempts < maxAttempts) {
      const status = await this.getJobStatus(jobId);

      if (status.status === 'COMPLETED') {
        return status;
      }

      if (status.status === 'FAILED') {
        throw new Error(status.errorMessage || 'Job processing failed');
      }

      // Wait before next poll
      await new Promise(resolve => setTimeout(resolve, intervalMs));
      attempts++;
    }

    throw new Error('Job processing timed out');
  }

  /**
   * Get invoice by ID
   */
  async getInvoice(invoiceId: string): Promise<Invoice> {
    const response = await fetch(`${API_BASE_URL}/api/invoices/${invoiceId}`, {
      headers: getAuthHeaders(),
    });

    if (!response.ok) {
      throw new Error(`Failed to get invoice: ${response.statusText}`);
    }

    return await response.json();
  }

  /**
   * Get goods receipt by ID
   */
  async getGoodsReceipt(grId: string): Promise<GoodsReceipt> {
    const response = await fetch(`${API_BASE_URL}/api/goods-receipts/${grId}`, {
      headers: getAuthHeaders(),
    });

    if (!response.ok) {
      throw new Error(`Failed to get goods receipt: ${response.statusText}`);
    }

    return await response.json();
  }

  /**
   * Complete invoice upload workflow:
   * 1. Upload file
   * 2. Poll for completion
   * 3. Return invoice data
   */
  async uploadInvoiceAndWait(file: File): Promise<Invoice> {
    // Step 1: Upload file
    const uploadResult = await this.uploadInvoice(file);

    // Step 2: Poll for completion
    const jobStatus = await this.pollForJobCompletion(uploadResult.jobId);

    // Step 3: Get invoice data
    if (!jobStatus.resultId) {
      throw new Error('Job completed but no result ID returned');
    }

    return await this.getInvoice(jobStatus.resultId);
  }

  /**
   * Complete GR upload workflow:
   * 1. Upload file
   * 2. Poll for completion
   * 3. Return GR data
   */
  async uploadGoodsReceiptAndWait(file: File): Promise<GoodsReceipt> {
    // Step 1: Upload file
    const uploadResult = await this.uploadGoodsReceipt(file);

    // Step 2: Poll for completion
    const jobStatus = await this.pollForJobCompletion(uploadResult.jobId);

    // Step 3: Get GR data
    if (!jobStatus.resultId) {
      throw new Error('Job completed but no result ID returned');
    }

    return await this.getGoodsReceipt(jobStatus.resultId);
  }
}

// Export singleton instance
export const documentService = new DocumentService();
