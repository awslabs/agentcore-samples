import type {
  Vendor,
  Delivery,
  Invoice,
  Payment,
  QualityCharacteristic,
} from '../types/data.types';

// Mock Vendors
export const mockVendors: Vendor[] = [
  {
    id: 'V-CHEM-001',
    code: 'V-CHEM-001',
    name: 'Chemical Suppliers Inc.',
    performanceScore: 92,
    onTimeDeliveryRate: 95,
    qualityScore: 90,
    invoiceAccuracy: 88,
  },
  {
    id: 'V-CHEM-002',
    code: 'V-CHEM-002',
    name: 'Global Chemical Solutions',
    performanceScore: 88,
    onTimeDeliveryRate: 90,
    qualityScore: 85,
    invoiceAccuracy: 92,
  },
  {
    id: 'V-CHEM-003',
    code: 'V-CHEM-003',
    name: 'Premium Materials Ltd.',
    performanceScore: 85,
    onTimeDeliveryRate: 88,
    qualityScore: 82,
    invoiceAccuracy: 85,
  },
];

// Mock Purchase Orders (legacy - using enhanced ones in api.service.ts)
export const mockPurchaseOrders: any[] = [
  {
    id: 'PO-2025-00052',
    poNumber: 'PO-2025-00052',
    vendor: mockVendors[0],
    sourceLocation: 'Offshore-Platform-KG12',
    assignedDate: new Date('2025-07-23'),
    status: 'active',
    items: [
      {
        id: '1',
        description: 'Industrial Chemical Grade A',
        quantity: 1000,
        receivedQuantity: 0,
        unitPrice: 25.5,
        totalPrice: 25500,
      },
    ],
    totalAmount: 25500,
    attachments: { poDocuments: 1, receipts: 0, invoices: 0, total: 1 },
  },
  {
    id: 'PO-2025-00053',
    poNumber: 'PO-2025-00053',
    vendor: mockVendors[1],
    sourceLocation: 'Gas Processing - LA02',
    assignedDate: new Date('2025-07-24'),
    status: 'active',
    items: [
      {
        id: '1',
        description: 'Processing Equipment Parts',
        quantity: 50,
        receivedQuantity: 0,
        unitPrice: 450,
        totalPrice: 22500,
      },
    ],
    totalAmount: 22500,
    attachments: { poDocuments: 1, receipts: 0, invoices: 0, total: 1 },
  },
  {
    id: 'PO-2025-00054',
    poNumber: 'PO-2025-00054',
    vendor: mockVendors[2],
    sourceLocation: 'Onshore-DrillRig-Permian',
    assignedDate: new Date('2025-07-25'),
    status: 'active',
    items: [
      {
        id: '1',
        description: 'Drilling Fluids',
        quantity: 2000,
        receivedQuantity: 0,
        unitPrice: 15.75,
        totalPrice: 31500,
      },
    ],
    totalAmount: 31500,
    attachments: { poDocuments: 1, receipts: 0, invoices: 0, total: 1 },
  },
  {
    id: 'PO-2025-00055',
    poNumber: 'PO-2025-00055',
    vendor: mockVendors[0],
    sourceLocation: 'Refinery-TX01',
    assignedDate: new Date('2025-07-26'),
    status: 'active',
    items: [
      {
        id: '1',
        description: 'Refinery Catalysts',
        quantity: 500,
        receivedQuantity: 0,
        unitPrice: 85,
        totalPrice: 42500,
      },
    ],
    totalAmount: 42500,
    attachments: { poDocuments: 1, receipts: 0, invoices: 0, total: 1 },
  },
  {
    id: 'PO-2025-00056',
    poNumber: 'PO-2025-00056',
    vendor: mockVendors[1],
    sourceLocation: 'Pipeline Maint - OH',
    assignedDate: new Date('2025-07-27'),
    status: 'active',
    items: [
      {
        id: '1',
        description: 'Pipeline Coating Materials',
        quantity: 1500,
        receivedQuantity: 0,
        unitPrice: 12.5,
        totalPrice: 18750,
      },
    ],
    totalAmount: 18750,
    attachments: { poDocuments: 1, receipts: 0, invoices: 0, total: 1 },
  },
];

// Mock Deliveries
export const mockDeliveries: Delivery[] = [
  {
    id: 'DEL-001',
    goodsReceiptId: 'GR-001',
    purchaseOrderId: 'PO-2025-00052',
    vendor: mockVendors[0],
    deliveryDate: new Date('2025-07-28'),
    status: 'awaiting_qc',
  },
  {
    id: 'DEL-002',
    goodsReceiptId: 'GR-002',
    purchaseOrderId: 'PO-2025-00053',
    vendor: mockVendors[1],
    deliveryDate: new Date('2025-07-29'),
    status: 'awaiting_qc',
  },
  {
    id: 'DEL-003',
    goodsReceiptId: 'GR-003',
    purchaseOrderId: 'PO-2025-00054',
    vendor: mockVendors[2],
    deliveryDate: new Date('2025-07-30'),
    status: 'qc_completed',
  },
];

// Mock Quality Characteristics
export const mockQualityCharacteristics: QualityCharacteristic[] = [
  {
    id: 'QC-001',
    name: 'Purity Level (%)',
    expectedValue: '99.5',
    inputType: 'number',
  },
  {
    id: 'QC-002',
    name: 'pH Level',
    expectedValue: '7.0',
    inputType: 'number',
  },
  {
    id: 'QC-003',
    name: 'Visual Inspection',
    expectedValue: 'Pass',
    inputType: 'select',
    options: ['Pass', 'Fail'],
  },
  {
    id: 'QC-004',
    name: 'Packaging Condition',
    expectedValue: 'Intact',
    inputType: 'select',
    options: ['Intact', 'Damaged', 'Acceptable'],
  },
  {
    id: 'QC-005',
    name: 'Temperature (°C)',
    expectedValue: '20-25',
    inputType: 'text',
  },
];

// Mock Invoices
export const mockInvoices: Invoice[] = [
  {
    id: 'INV-001',
    invoiceNumber: 'INV-2025-1001',
    vendor: mockVendors[0],
    purchaseOrderId: 'PO-2025-00052',
    invoiceDate: new Date('2025-08-01'),
    dueDate: new Date('2025-08-31'),
    amount: 25800,
    status: 'pending_review',
    mismatchType: 'price',
  },
  {
    id: 'INV-002',
    invoiceNumber: 'INV-2025-1002',
    vendor: mockVendors[1],
    purchaseOrderId: 'PO-2025-00053',
    invoiceDate: new Date('2025-08-02'),
    dueDate: new Date('2025-09-01'),
    amount: 22300,
    status: 'pending_review',
    mismatchType: 'quantity',
  },
  {
    id: 'INV-003',
    invoiceNumber: 'INV-2025-1003',
    vendor: mockVendors[2],
    purchaseOrderId: 'PO-2025-00054',
    invoiceDate: new Date('2025-08-03'),
    dueDate: new Date('2025-09-02'),
    amount: 31500,
    status: 'approved',
  },
  {
    id: 'INV-004',
    invoiceNumber: 'INV-2025-1004',
    vendor: mockVendors[0],
    purchaseOrderId: 'PO-2025-00055',
    invoiceDate: new Date('2025-08-04'),
    dueDate: new Date('2025-09-03'),
    amount: 42800,
    status: 'pending_review',
    mismatchType: 'multiple',
  },
];

// Mock Payments
export const mockPayments: Payment[] = [
  {
    id: 'PAY-001',
    invoiceId: 'INV-003',
    vendor: mockVendors[2],
    amount: 31500,
    scheduledDate: new Date('2025-09-02'),
    paymentMethod: 'Wire Transfer',
    status: 'scheduled',
  },
  {
    id: 'PAY-002',
    invoiceId: 'INV-001',
    vendor: mockVendors[0],
    amount: 25500,
    scheduledDate: new Date('2025-08-15'),
    paymentMethod: 'ACH',
    status: 'processing',
  },
  {
    id: 'PAY-003',
    invoiceId: 'INV-002',
    vendor: mockVendors[1],
    amount: 22500,
    scheduledDate: new Date('2025-08-10'),
    paymentMethod: 'Wire Transfer',
    status: 'completed',
  },
];
