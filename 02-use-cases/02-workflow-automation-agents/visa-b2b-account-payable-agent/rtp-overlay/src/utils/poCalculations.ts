import type { PurchaseOrder, MatchStatus } from '../types/data.types';

/**
 * Calculate fulfillment percentage for a PO
 */
export const calculateFulfillmentPercentage = (
  receivedQuantity: number,
  orderedQuantity: number
): number => {
  if (orderedQuantity === 0) return 0;
  return Math.min((receivedQuantity / orderedQuantity) * 100, 100);
};

/**
 * Calculate remaining amount (not yet invoiced)
 */
export const calculateRemainingAmount = (
  totalAmount: number,
  invoicedAmount: number = 0
): number => {
  return Math.max(totalAmount - invoicedAmount, 0);
};

/**
 * Determine match status based on documents
 */
export const determineMatchStatus = (
  hasReceipt: boolean,
  hasInvoice: boolean,
  variancePercentage: number,
  toleranceThreshold: number = 10
): MatchStatus => {
  // No documents
  if (!hasReceipt && !hasInvoice) {
    return 'no-docs';
  }
  
  // Exception if variance exceeds tolerance
  if (variancePercentage > toleranceThreshold) {
    return 'exception';
  }
  
  // 3-way match: PO + Receipt + Invoice
  if (hasReceipt && hasInvoice) {
    return '3-way';
  }
  
  // 2-way match: PO + Invoice (no receipt yet)
  if (hasInvoice) {
    return '2-way';
  }
  
  // Only receipt, no invoice yet
  return 'no-docs';
};

/**
 * Check if PO is overdue
 */
export const isOverdue = (dueDate: Date | string, status: string): boolean => {
  // Not overdue if already received or closed
  if (status === 'received' || status === 'closed') {
    return false;
  }
  
  const due = typeof dueDate === 'string' ? new Date(dueDate) : dueDate;
  const today = new Date();
  today.setHours(0, 0, 0, 0); // Reset time for date-only comparison
  
  return due < today;
};

/**
 * Format amount display for Total/Remaining column
 */
export const formatAmountDisplay = (
  totalAmount: number,
  remainingAmount: number,
  currency: string = 'USD'
): string => {
  const formatter = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: currency,
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
  
  return `${formatter.format(totalAmount)} / ${formatter.format(remainingAmount)}`;
};

/**
 * Get color for remaining amount based on status
 */
export const getRemainingAmountColor = (
  remainingAmount: number,
  totalAmount: number
): 'success' | 'warning' | 'error' => {
  if (remainingAmount === 0) return 'success'; // Fully invoiced
  if (remainingAmount < totalAmount) return 'warning'; // Partially invoiced
  if (remainingAmount > totalAmount) return 'error'; // Over-invoiced (shouldn't happen)
  return 'warning';
};

/**
 * Get color for fulfillment progress
 */
export const getFulfillmentColor = (percentage: number): 'success' | 'primary' | 'default' => {
  if (percentage >= 100) return 'success';
  if (percentage > 0) return 'primary';
  return 'default';
};

/**
 * Get color for match status chip
 */
export const getMatchStatusColor = (
  status: MatchStatus
): 'default' | 'primary' | 'success' | 'error' => {
  const colors: Record<MatchStatus, 'default' | 'primary' | 'success' | 'error'> = {
    'no-docs': 'default',
    '2-way': 'primary',
    '3-way': 'success',
    'exception': 'error',
  };
  return colors[status];
};

/**
 * Get label for match status
 */
export const getMatchStatusLabel = (status: MatchStatus): string => {
  const labels: Record<MatchStatus, string> = {
    'no-docs': 'No Docs',
    '2-way': '2-Way OK',
    '3-way': '3-Way OK',
    'exception': 'Exception',
  };
  return labels[status];
};

/**
 * Calculate total ordered quantity from items
 */
export const calculateOrderedQuantity = (items: { quantity: number }[]): number => {
  return items.reduce((sum, item) => sum + item.quantity, 0);
};

/**
 * Calculate total received quantity from items
 */
export const calculateReceivedQuantity = (items: { receivedQuantity: number }[]): number => {
  return items.reduce((sum, item) => sum + item.receivedQuantity, 0);
};

/**
 * Check if PO has partial receipt
 */
export const hasPartialReceipt = (receivedQuantity: number, orderedQuantity: number): boolean => {
  return receivedQuantity > 0 && receivedQuantity < orderedQuantity;
};

/**
 * Enrich PO with calculated fields
 */
export const enrichPurchaseOrder = (po: Partial<PurchaseOrder>): PurchaseOrder => {
  const orderedQuantity = po.items ? calculateOrderedQuantity(po.items) : 0;
  const receivedQuantity = po.items ? calculateReceivedQuantity(po.items) : 0;
  const fulfillmentPercentage = calculateFulfillmentPercentage(receivedQuantity, orderedQuantity);
  const isOverdueFlag = po.dueDate && po.status ? isOverdue(po.dueDate, po.status) : false;
  const hasPartialReceiptFlag = hasPartialReceipt(receivedQuantity, orderedQuantity);
  
  return {
    ...po,
    orderedQuantity,
    receivedQuantity,
    fulfillmentPercentage,
    isOverdue: isOverdueFlag,
    hasPartialReceipt: hasPartialReceiptFlag,
  } as PurchaseOrder;
};
