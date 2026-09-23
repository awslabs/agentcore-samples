import type {
  PurchaseOrder,
  GoodsReceipt,
  GoodsReceiptData,
  Delivery,
  QualityCharacteristic,
  QualityInspection,
  QCData,
  Invoice,
  MismatchDetail,
  Payment,
  Vendor,
} from '../types/data.types';
import {
  mockPurchaseOrders,
  mockDeliveries,
  mockQualityCharacteristics,
  mockInvoices,
  mockPayments,
  mockVendors,
} from './mockData';

// API Configuration
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

// Fail fast if API URL is not configured
if (!API_BASE_URL) {
  console.error('❌ VITE_API_BASE_URL is not set in .env file');
  console.error('Create a .env file with: VITE_API_BASE_URL=https://your-api-gateway-url/prod');
}

// Get auth headers with JWT token
const getAuthHeaders = (): HeadersInit => {
  const token = localStorage.getItem('accessToken');
  return {
    'Authorization': token ? `Bearer ${token}` : '',
    'Content-Type': 'application/json',
  };
};

// Simulate API delay
const delay = (ms: number = 500) => new Promise((resolve) => setTimeout(resolve, ms));

// Purchase Orders - Connected to real API
export const getPurchaseOrders = async (): Promise<PurchaseOrder[]> => {
  try {
    const response = await fetch(`${API_BASE_URL}/api/purchase-orders?status=active&limit=100`, {
      headers: getAuthHeaders(),
    });

    if (!response.ok) {
      throw new Error(`Failed to fetch purchase orders: ${response.statusText}`);
    }

    const result = await response.json();
    
    // Backend returns paginated response: { data: [...], pagination: {...} }
    const purchaseOrders = result.data || result;
    
    // Transform backend data to match frontend PurchaseOrder type
    return purchaseOrders.map((po: any) => ({
      id: po.id,
      poNumber: po.poNumber,
      vendor: po.vendor,
      sourceLocation: po.sourceLocation,
      assignedDate: po.assignedDate,
      status: po.status,
      totalAmount: parseFloat(po.totalAmount),
      items: po.items || [],
    }));
  } catch (error) {
    console.error('Error fetching purchase orders:', error);
    // Fallback to mock data if API fails
    return mockPurchaseOrders.filter((po) => po.status === 'active');
  }
};

export const getPurchaseOrderById = async (id: string): Promise<PurchaseOrder | null> => {
  try {
    const response = await fetch(`${API_BASE_URL}/api/purchase-orders/${id}`, {
      headers: getAuthHeaders(),
    });

    if (!response.ok) {
      if (response.status === 404) {
        return null;
      }
      throw new Error(`Failed to fetch purchase order: ${response.statusText}`);
    }

    const po = await response.json();
    
    // Transform backend data to match frontend PurchaseOrder type
    // For now, return from enhanced mock data
    const mockPO = mockPurchaseOrders.find(p => p.id === id);
    if (mockPO) {
      return mockPO;
    }
    
    // Fallback to basic transformation if not in mock data
    return {
      id: po.id,
      poNumber: po.poNumber,
      vendor: po.vendor,
      sourceLocation: po.sourceLocation,
      assignedDate: new Date(po.assignedDate),
      dueDate: new Date(po.dueDate || Date.now()),
      warehouse: po.warehouse,
      status: po.status,
      currency: po.currency || 'USD',
      items: po.items || [],
      totalAmount: parseFloat(po.totalAmount),
      orderedQuantity: po.orderedQuantity || 0,
      receivedQuantity: po.receivedQuantity || 0,
      fulfillmentPercentage: po.fulfillmentPercentage || 0,
      hasPartialReceipt: po.hasPartialReceipt || false,
      remainingAmount: po.remainingAmount || parseFloat(po.totalAmount),
      matchStatus: po.matchStatus || 'no-docs',
      hasReceipt: po.hasReceipt || false,
      hasInvoice: po.hasInvoice || false,
      variancePercentage: po.variancePercentage || 0,
      attachments: po.attachments || { poDocuments: 1, receipts: 0, invoices: 0, total: 1 },
      isOverdue: po.isOverdue || false,
    };
  } catch (error) {
    console.error('Error fetching purchase order:', error);
    // Fallback to mock data if API fails
    return mockPurchaseOrders.find((po) => po.id === id) || null;
  }
};

// Enhanced PO methods
export const getPurchaseOrdersEnhanced = async (_filters?: any): Promise<PurchaseOrder[]> => {
  try {
    const response = await fetch(`${API_BASE_URL}/api/purchase-orders`, {
      headers: getAuthHeaders(),
    });

    if (!response.ok) {
      console.warn(`API returned ${response.status}: ${response.statusText}`);
      
      // If 500 error, log more details
      if (response.status === 500) {
        const errorText = await response.text();
        console.error('500 Error details:', errorText);
        console.warn('Falling back to mock data. Database may need seeding.');
      }
      
      throw new Error(`Failed to fetch purchase orders: ${response.statusText}`);
    }

    const result = await response.json();
    
    // Handle paginated response format {data: [...], pagination: {...}}
    const purchaseOrders = result.data || result;
    
    // If empty array from backend, use mock data
    if (!Array.isArray(purchaseOrders) || purchaseOrders.length === 0) {
      console.warn('No purchase orders returned from API. Using mock data. Database may need seeding.');
      return mockPurchaseOrders;
    }
    
    // Transform to match frontend format if needed
    return purchaseOrders.map((po: any) => ({
      ...po,
      vendor: po.vendor || { id: po.vendorId, name: 'Unknown', code: 'N/A' },
      dueDate: po.dueDate ? new Date(po.dueDate) : new Date(),
      assignedDate: new Date(po.assignedDate),
      totalAmount: Number(po.totalAmount),
      orderedQuantity: po.orderedQuantity || po.items?.reduce((sum: number, item: any) => sum + Number(item.quantity), 0) || 0,
      receivedQuantity: po.receivedQuantity || po.items?.reduce((sum: number, item: any) => sum + Number(item.receivedQuantity || 0), 0) || 0,
      fulfillmentPercentage: po.fulfillmentPercentage || 0,
      remainingAmount: po.remainingAmount || Number(po.totalAmount),
      currency: po.currency || 'USD',
      warehouse: po.warehouse || po.sourceLocation,
      hasReceipt: po.hasReceipt || false,
      hasPartialReceipt: po.hasPartialReceipt || false,
      hasInvoice: po.hasInvoice || false,
      matchStatus: po.matchStatus || 'no-docs',
      variancePercentage: po.variancePercentage || 0,
      attachments: po.attachments || { poDocuments: 1, receipts: 0, invoices: 0, total: 1 },
      isOverdue: po.isOverdue || false,
    }));
  } catch (error) {
    console.error('Error fetching purchase orders:', error);
    console.warn('Falling back to mock data');
    // Fallback to mock data if API fails
    return mockPurchaseOrders;
  }
};

export const createPurchaseOrder = async (poData: any): Promise<PurchaseOrder> => {
  try {
    // Transform frontend data to backend format
    const backendData = {
      poNumber: poData.poNumber,
      vendorId: poData.vendor.id, // Extract vendor ID from vendor object
      sourceLocation: poData.sourceLocation,
      assignedDate: new Date().toISOString().split('T')[0], // Today's date in YYYY-MM-DD format
      dueDate: poData.dueDate,
      warehouse: poData.warehouse,
      totalAmount: poData.items.reduce((sum: number, item: any) => sum + item.totalPrice, 0),
      currency: 'USD',
      status: 'active',
      items: poData.items.map((item: any) => ({
        description: item.description,
        quantity: item.quantity,
        unitPrice: item.unitPrice,
        totalPrice: item.totalPrice,
        receivedQuantity: 0,
      })),
    };

    const response = await fetch(`${API_BASE_URL}/api/purchase-orders`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(backendData),
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error('Backend error:', errorText);
      throw new Error(`Failed to create purchase order: ${response.statusText}`);
    }

    const result = await response.json();
    return result;
  } catch (error) {
    console.error('Error creating purchase order:', error);
    throw error;
  }
};

export const uploadReceiptForPO = async (
  poId: string,
  _file: File,
  metadata: any
): Promise<{ message: string; receiptId: string }> => {
  await delay();
  const receiptId = `receipt-${Date.now()}`;
  // Mock implementation - update PO
  const poIndex = mockPurchaseOrders.findIndex(po => po.id === poId);
  if (poIndex !== -1) {
    const po = mockPurchaseOrders[poIndex];
    po.receivedQuantity += metadata.quantityReceived;
    po.fulfillmentPercentage = (po.receivedQuantity / po.orderedQuantity) * 100;
    po.hasPartialReceipt = po.receivedQuantity > 0 && po.receivedQuantity < po.orderedQuantity;
    po.hasReceipt = true;
    po.attachments.receipts += 1;
    po.attachments.total += 1;
  }
  return { message: 'Receipt uploaded successfully', receiptId };
};

export const uploadInvoiceForPO = async (
  poId: string,
  _file: File,
  metadata: any
): Promise<{ message: string; invoiceId: string }> => {
  await delay();
  const invoiceId = `invoice-${Date.now()}`;
  // Mock implementation - update PO
  const poIndex = mockPurchaseOrders.findIndex(po => po.id === poId);
  if (poIndex !== -1) {
    const po = mockPurchaseOrders[poIndex];
    po.hasInvoice = true;
    po.attachments.invoices += 1;
    po.attachments.total += 1;
    po.variancePercentage = Math.abs((metadata.invoiceAmount - po.totalAmount) / po.totalAmount) * 100;
    po.matchStatus = po.hasReceipt ? '3-way' : '2-way';
    po.remainingAmount = Math.max(po.totalAmount - metadata.invoiceAmount, 0);
  }
  return { message: 'Invoice uploaded successfully', invoiceId };
};

// Goods Receipts
export const createGoodsReceipt = async (
  purchaseOrderId: string,
  data: GoodsReceiptData,
  _receivedBy: string
): Promise<GoodsReceipt> => {
  try {
    const response = await fetch(`${API_BASE_URL}/api/goods-receipts`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({
        purchaseOrderId,
        deliveryDate: data.deliveryDate.toISOString(),
        quantityReceived: data.quantityReceived,
        conditionNotes: data.conditionNotes,
        deliveryReference: data.deliveryReference,
      }),
    });

    if (!response.ok) {
      throw new Error(`Failed to create goods receipt: ${response.statusText}`);
    }

    const result = await response.json();
    return result;
  } catch (error) {
    console.error('Error creating goods receipt:', error);
    throw error;
  }
};

export const getGoodsReceipts = async (): Promise<GoodsReceipt[]> => {
  try {
    const response = await fetch(`${API_BASE_URL}/api/goods-receipts?limit=100`, {
      headers: getAuthHeaders(),
    });

    if (!response.ok) {
      throw new Error(`Failed to fetch goods receipts: ${response.statusText}`);
    }

    const result = await response.json();
    const receipts = result.data || result;
    
    // Transform backend data to match frontend types
    return receipts.map((gr: any) => ({
      id: gr.id,
      purchaseOrderId: gr.purchaseOrderId,
      purchaseOrder: gr.purchaseOrder ? {
        id: gr.purchaseOrder.id,
        poNumber: gr.purchaseOrder.poNumber,
        vendor: gr.purchaseOrder.vendor,
      } : undefined,
      deliveryDate: new Date(gr.deliveryDate),
      quantityReceived: gr.quantityReceived,
      conditionNotes: gr.conditionNotes || '',
      deliveryReference: gr.deliveryReference,
      receivedBy: gr.receivedBy?.username || gr.receivedBy || 'Unknown',
      status: gr.status,
      createdAt: gr.createdAt ? new Date(gr.createdAt) : undefined,
      updatedAt: gr.updatedAt ? new Date(gr.updatedAt) : undefined,
    }));
  } catch (error) {
    console.error('Error fetching goods receipts:', error);
    throw error; // Re-throw to allow component to handle error
  }
};

// Deliveries
export const getDeliveries = async (): Promise<Delivery[]> => {
  await delay();
  return mockDeliveries.filter((d) => d.status === 'awaiting_qc');
};

export const getDeliveryById = async (id: string): Promise<Delivery | null> => {
  await delay();
  return mockDeliveries.find((d) => d.id === id) || null;
};

// Quality Control
export const getQualityCharacteristics = async (): Promise<QualityCharacteristic[]> => {
  await delay();
  return mockQualityCharacteristics;
};

let qualityInspections: QualityInspection[] = [];

export const createQualityInspection = async (
  deliveryId: string,
  data: QCData,
  inspector: string
): Promise<QualityInspection> => {
  await delay();

  const overallResult = data.characteristics.every((c) => c.result === 'pass')
    ? 'pass'
    : 'fail';

  const newInspection: QualityInspection = {
    id: `QI-${Date.now()}`,
    deliveryId,
    inspectionDate: new Date(),
    inspector,
    characteristics: data.characteristics,
    overallResult,
    notes: data.notes,
  };

  qualityInspections.push(newInspection);
  return newInspection;
};

export const getQualityInspections = async (): Promise<QualityInspection[]> => {
  await delay();
  return qualityInspections;
};

// Invoices
export const getInvoices = async (): Promise<Invoice[]> => {
  await delay();
  return mockInvoices;
};

export const getInvoiceById = async (id: string): Promise<Invoice | null> => {
  await delay();
  return mockInvoices.find((inv) => inv.id === id) || null;
};

export const getInvoiceMismatchDetails = async (_invoiceId: string): Promise<MismatchDetail[]> => {
  await delay();

  // Mock mismatch details
  return [
    {
      field: 'Quantity',
      poValue: 1000,
      grValue: 1000,
      qcValue: 1000,
      invoiceValue: 1020,
      variance: 20,
    },
    {
      field: 'Unit Price',
      poValue: 25.5,
      grValue: 25.5,
      qcValue: 25.5,
      invoiceValue: 25.8,
      variance: 0.3,
    },
  ];
};

export const approveInvoice = async (invoiceId: string): Promise<void> => {
  await delay();
  const invoice = mockInvoices.find((inv) => inv.id === invoiceId);
  if (invoice) {
    invoice.status = 'approved';
  }
};

export const disputeInvoice = async (invoiceId: string, reason: string): Promise<void> => {
  await delay();
  const invoice = mockInvoices.find((inv) => inv.id === invoiceId);
  if (invoice) {
    invoice.status = 'disputed';
  }
  console.log(`Invoice ${invoiceId} disputed: ${reason}`);
};

// Payments
export const getPayments = async (): Promise<Payment[]> => {
  await delay();
  return mockPayments;
};

export const getPaymentById = async (id: string): Promise<Payment | null> => {
  await delay();
  return mockPayments.find((p) => p.id === id) || null;
};

// Vendors
export const getVendors = async (): Promise<Vendor[]> => {
  try {
    const response = await fetch(`${API_BASE_URL}/api/vendors`, {
      headers: getAuthHeaders(),
    });

    if (!response.ok) {
      console.warn('Failed to fetch vendors from backend, using mock data');
      return mockVendors;
    }

    const result = await response.json();
    // Handle both array and object responses
    const vendors = Array.isArray(result) ? result : (result.data || []);
    
    if (!Array.isArray(vendors) || vendors.length === 0) {
      console.warn('No vendors returned from backend, using mock data');
      return mockVendors;
    }
    
    return vendors.map((v: any) => ({
      id: v.id,
      code: v.code,
      name: v.name,
      performanceScore: v.performanceScore || 0,
      onTimeDeliveryRate: v.onTimeDeliveryRate || 0,
      qualityScore: v.qualityScore || 0,
      invoiceAccuracy: v.invoiceAccuracy || 0,
    }));
  } catch (error) {
    console.error('Error fetching vendors:', error);
    return mockVendors;
  }
};

export const getVendorById = async (id: string): Promise<Vendor | null> => {
  try {
    const response = await fetch(`${API_BASE_URL}/api/vendors/${id}`, {
      headers: getAuthHeaders(),
    });

    if (!response.ok) {
      return mockVendors.find((v) => v.id === id) || null;
    }

    return await response.json();
  } catch (error) {
    console.error('Error fetching vendor:', error);
    return mockVendors.find((v) => v.id === id) || null;
  }
};
