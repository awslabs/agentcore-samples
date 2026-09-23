import React, { createContext, useContext, useState, useCallback } from 'react';
import type {
  Payment,
  PaymentFilters,
  PaymentSummary,
  PaginationState,
} from '../types/payment.types';
import { paymentService } from '../services/paymentService';

interface PaymentState {
  payments: Payment[];
  currentPayment: Payment | null;
  loading: boolean;
  error: string | null;
  filters: PaymentFilters;
  pagination: PaginationState;
  summary: PaymentSummary | null;
}

interface PaymentContextValue extends PaymentState {
  // Actions
  fetchPayments: (filters?: PaymentFilters, page?: number) => Promise<void>;
  fetchPaymentById: (id: string) => Promise<void>;
  downloadISO20022File: (id: string) => Promise<void>;
  setFilters: (filters: PaymentFilters) => void;
  clearFilters: () => void;
  setPage: (page: number) => void;
  fetchSummary: () => Promise<void>;
}

const defaultFilters: PaymentFilters = {
  status: [],
  vendorId: null,
  dueDateRange: { start: null, end: null },
  amountRange: { min: null, max: null },
  overdueOnly: false,
  search: '',
};

const PaymentContext = createContext<PaymentContextValue | undefined>(
  undefined
);

export const PaymentProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [state, setState] = useState<PaymentState>({
    payments: [],
    currentPayment: null,
    loading: false,
    error: null,
    filters: defaultFilters,
    pagination: { page: 1, limit: 10, total: 0 },
    summary: null,
  });

  /**
   * Fetch payments with filters and pagination
   */
  const fetchPayments = useCallback(
    async (filters?: PaymentFilters, page?: number) => {
      setState((prev) => ({ ...prev, loading: true, error: null }));

      try {
        const currentFilters = filters || state.filters;
        const currentPage = page || state.pagination.page;

        const response = await paymentService.getPayments({
          page: currentPage,
          limit: state.pagination.limit,
          status: currentFilters.status.length
            ? currentFilters.status
            : undefined,
          vendorId: currentFilters.vendorId || undefined,
          dueDateStart: currentFilters.dueDateRange.start || undefined,
          dueDateEnd: currentFilters.dueDateRange.end || undefined,
          amountMin: currentFilters.amountRange.min || undefined,
          amountMax: currentFilters.amountRange.max || undefined,
          overdueOnly: currentFilters.overdueOnly || undefined,
          search: currentFilters.search || undefined,
        });

        setState((prev) => ({
          ...prev,
          payments: response.data,
          pagination: response.pagination,
          loading: false,
        }));
      } catch (error) {
        setState((prev) => ({
          ...prev,
          error:
            error instanceof Error ? error.message : 'Failed to fetch payments',
          loading: false,
        }));
      }
    },
    [state.filters, state.pagination.page, state.pagination.limit]
  );

  /**
   * Fetch a single payment by ID
   */
  const fetchPaymentById = useCallback(async (id: string) => {
    setState((prev) => ({ ...prev, loading: true, error: null }));

    try {
      const payment = await paymentService.getPaymentById(id);

      setState((prev) => ({
        ...prev,
        currentPayment: payment,
        loading: false,
      }));
    } catch (error) {
      setState((prev) => ({
        ...prev,
        error:
          error instanceof Error ? error.message : 'Failed to fetch payment',
        loading: false,
      }));
    }
  }, []);

  /**
   * Download ISO 20022 XML file
   */
  const downloadISO20022File = useCallback(async (id: string) => {
    try {
      const url = await paymentService.getISO20022FileUrl(id);

      // Open URL in new tab to trigger download
      window.open(url, '_blank');
    } catch (error) {
      setState((prev) => ({
        ...prev,
        error:
          error instanceof Error
            ? error.message
            : 'Failed to download ISO 20022 file',
      }));
      throw error;
    }
  }, []);

  /**
   * Fetch payment summary statistics
   */
  const fetchSummary = useCallback(async () => {
    try {
      const summary = await paymentService.getPaymentSummary();

      setState((prev) => ({
        ...prev,
        summary,
      }));
    } catch (error) {
      console.error('Failed to fetch payment summary:', error);
      // Don't set error state for summary failures
    }
  }, []);

  /**
   * Update filters
   */
  const setFilters = useCallback(
    (filters: PaymentFilters) => {
      setState((prev) => ({
        ...prev,
        filters,
        pagination: { ...prev.pagination, page: 1 }, // Reset to page 1
      }));

      // Fetch payments with new filters
      fetchPayments(filters, 1);
    },
    [fetchPayments]
  );

  /**
   * Clear all filters
   */
  const clearFilters = useCallback(() => {
    setState((prev) => ({
      ...prev,
      filters: defaultFilters,
      pagination: { ...prev.pagination, page: 1 },
    }));

    // Fetch payments with default filters
    fetchPayments(defaultFilters, 1);
  }, [fetchPayments]);

  /**
   * Set current page
   */
  const setPage = useCallback(
    (page: number) => {
      setState((prev) => ({
        ...prev,
        pagination: { ...prev.pagination, page },
      }));

      // Fetch payments for new page
      fetchPayments(state.filters, page);
    },
    [fetchPayments, state.filters]
  );

  const contextValue: PaymentContextValue = {
    ...state,
    fetchPayments,
    fetchPaymentById,
    downloadISO20022File,
    setFilters,
    clearFilters,
    setPage,
    fetchSummary,
  };

  return (
    <PaymentContext.Provider value={contextValue}>
      {children}
    </PaymentContext.Provider>
  );
};

/**
 * Custom hook to use Payment Context
 */
export const usePayment = (): PaymentContextValue => {
  const context = useContext(PaymentContext);

  if (!context) {
    throw new Error('usePayment must be used within PaymentProvider');
  }

  return context;
};
