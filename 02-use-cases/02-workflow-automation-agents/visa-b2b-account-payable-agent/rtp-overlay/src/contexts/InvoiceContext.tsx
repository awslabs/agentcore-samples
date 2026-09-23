import React, { createContext, useContext, useState, useCallback } from 'react';
import type { ReactNode } from 'react';
import type {
  Invoice,
  InvoiceFilters,
  PaginationState,
  InvoiceSummary,
  PaymentStatus,
} from '../types/invoice.types';
import { invoiceService } from '../services/invoice.service';

interface InvoiceContextValue {
  // State
  invoices: Invoice[];
  currentInvoice: Invoice | null;
  loading: boolean;
  error: string | null;
  filters: InvoiceFilters;
  pagination: PaginationState;
  summary: InvoiceSummary | null;

  // Actions
  fetchInvoices: (filters?: InvoiceFilters, page?: number) => Promise<void>;
  fetchInvoiceById: (id: string) => Promise<void>;
  updateInvoiceStatus: (id: string, status: PaymentStatus) => Promise<void>;
  linkInvoiceToPO: (invoiceId: string, poId: string) => Promise<void>;
  downloadISO20022File: (id: string) => Promise<void>;
  setFilters: (filters: InvoiceFilters) => void;
  clearFilters: () => void;
  setPage: (page: number) => void;
  fetchSummary: () => Promise<void>;
}

const InvoiceContext = createContext<InvoiceContextValue | undefined>(undefined);

interface InvoiceProviderProps {
  children: ReactNode;
}

const defaultFilters: InvoiceFilters = {
  status: [],
  vendorId: null,
  startDate: null,
  endDate: null,
  poMatchStatus: 'all',
};

const defaultPagination: PaginationState = {
  page: 1,
  limit: 10,
  total: 0,
  totalPages: 0,
};

export const InvoiceProvider: React.FC<InvoiceProviderProps> = ({ children }) => {
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [currentInvoice, setCurrentInvoice] = useState<Invoice | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFiltersState] = useState<InvoiceFilters>(defaultFilters);
  const [pagination, setPagination] = useState<PaginationState>(defaultPagination);
  const [summary, setSummary] = useState<InvoiceSummary | null>(null);

  /**
   * Fetch invoices with filters and pagination
   */
  const fetchInvoices = useCallback(
    async (newFilters?: InvoiceFilters, page?: number) => {
      setLoading(true);
      setError(null);

      try {
        const currentFilters = newFilters || filters;
        const currentPage = page || pagination.page;

        const params = {
          page: currentPage,
          limit: pagination.limit,
          status: currentFilters.status.length > 0 ? currentFilters.status : undefined,
          vendorId: currentFilters.vendorId || undefined,
          startDate: currentFilters.startDate || undefined,
          endDate: currentFilters.endDate || undefined,
          hasPoMatch:
            currentFilters.poMatchStatus === 'matched'
              ? true
              : currentFilters.poMatchStatus === 'unmatched'
                ? false
                : undefined,
          hasVarianceWarning:
            currentFilters.poMatchStatus === 'variance' ? true : undefined,
        };

        const response = await invoiceService.getInvoices(params);

        setInvoices(response.data);
        setPagination({
          page: currentPage,
          limit: pagination.limit,
          total: response.pagination.total,
          totalPages: response.pagination.totalPages,
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to fetch invoices';
        setError(message);
        console.error('Error fetching invoices:', err);
      } finally {
        setLoading(false);
      }
    },
    [filters, pagination.page, pagination.limit]
  );

  /**
   * Fetch a single invoice by ID
   */
  const fetchInvoiceById = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);

    try {
      const invoice = await invoiceService.getInvoiceById(id);
      setCurrentInvoice(invoice);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch invoice';
      setError(message);
      console.error('Error fetching invoice:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Update invoice payment status
   */
  const updateInvoiceStatus = useCallback(
    async (id: string, status: PaymentStatus) => {
      setLoading(true);
      setError(null);

      try {
        const updatedInvoice = await invoiceService.updateInvoiceStatus(id, status);

        // Update current invoice if it's the one being updated
        if (currentInvoice?.id === id) {
          setCurrentInvoice(updatedInvoice);
        }

        // Update in the list
        setInvoices((prev) =>
          prev.map((inv) => (inv.id === id ? updatedInvoice : inv))
        );
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to update invoice status';
        setError(message);
        console.error('Error updating invoice status:', err);
        throw err;
      } finally {
        setLoading(false);
      }
    },
    [currentInvoice]
  );

  /**
   * Link invoice to a purchase order
   */
  const linkInvoiceToPO = useCallback(
    async (invoiceId: string, poId: string) => {
      setLoading(true);
      setError(null);

      try {
        const updatedInvoice = await invoiceService.linkToPurchaseOrder(invoiceId, poId);

        // Update current invoice if it's the one being updated
        if (currentInvoice?.id === invoiceId) {
          setCurrentInvoice(updatedInvoice);
        }

        // Update in the list
        setInvoices((prev) =>
          prev.map((inv) => (inv.id === invoiceId ? updatedInvoice : inv))
        );
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to link invoice to PO';
        setError(message);
        console.error('Error linking invoice to PO:', err);
        throw err;
      } finally {
        setLoading(false);
      }
    },
    [currentInvoice]
  );

  /**
   * Download ISO 20022 XML file
   */
  const downloadISO20022File = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);

    try {
      const url = await invoiceService.getISO20022FileUrl(id);
      // Open in new tab or trigger download
      window.open(url, '_blank');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to download file';
      setError(message);
      console.error('Error downloading ISO 20022 file:', err);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Fetch summary statistics
   */
  const fetchSummary = useCallback(async () => {
    try {
      const summaryData = await invoiceService.getStatistics();
      setSummary(summaryData);
    } catch (err) {
      console.error('Error fetching summary:', err);
      // Don't set error state for summary - it's not critical
    }
  }, []);

  /**
   * Set filters and refetch invoices
   */
  const setFilters = useCallback(
    (newFilters: InvoiceFilters) => {
      setFiltersState(newFilters);
      fetchInvoices(newFilters, 1); // Reset to page 1 when filters change
    },
    [fetchInvoices]
  );

  /**
   * Clear all filters
   */
  const clearFilters = useCallback(() => {
    setFiltersState(defaultFilters);
    fetchInvoices(defaultFilters, 1);
  }, [fetchInvoices]);

  /**
   * Set page and refetch
   */
  const setPage = useCallback(
    (page: number) => {
      fetchInvoices(filters, page);
    },
    [filters, fetchInvoices]
  );

  const value: InvoiceContextValue = {
    invoices,
    currentInvoice,
    loading,
    error,
    filters,
    pagination,
    summary,
    fetchInvoices,
    fetchInvoiceById,
    updateInvoiceStatus,
    linkInvoiceToPO,
    downloadISO20022File,
    setFilters,
    clearFilters,
    setPage,
    fetchSummary,
  };

  return <InvoiceContext.Provider value={value}>{children}</InvoiceContext.Provider>;
};

export const useInvoice = (): InvoiceContextValue => {
  const context = useContext(InvoiceContext);
  if (context === undefined) {
    throw new Error('useInvoice must be used within an InvoiceProvider');
  }
  return context;
};
