/**
 * Mock Data Service for Oil & Gas P2P MVP
 * Provides realistic demo data for frontend development
 */

export interface Vendor {
  id: string;
  name: string;
  contactEmail: string;
  contactPhone: string;
  address?: string;
}

export interface QualitySpec {
  parameter: string;
  min?: number;
  max?: number;
  unit: string;
}

export interface PurchaseOrder {
  id: string;
  poNumber: string;
  vendorId: string;
  vendorName: string;
  materialDescription: string;
  quantity: number;
  unit: string;
  unitPrice: number;
  totalAmount: number;
  deliveryDate: string;
  status: 'OPEN' | 'PARTIALLY_RECEIVED' | 'FULLY_RECEIVED' | 'CLOSED';
  qualitySpecs: QualitySpec[];
  createdAt: string;
}

export interface GoodsReceipt {
  id: string;
  grNumber: string;
  poId: string;
  poNumber: string;
  vendorId: string;
  vendorName: string;
  materialDescription: string;
  receivedDate: string;
  receivedBy: string;
  receivedQuantity: number;
  unit: string;
  notes?: string;
  documentUrl?: string;
  extractionData?: any;
  confidenceScores?: Record<string, number>;
  matchStatus: 'PENDING' | 'MATCHED' | 'MISMATCHED';
  createdAt: string;
}

export interface QCMeasurement {
  parameter: string;
  value: number;
  unit: string;
  specification: string;
  status: 'PASS' | 'FAIL';
}

export interface QCRecord {
  id: string;
  qcNumber: string;
  poId: string;
  poNumber: string;
  grId?: string;
  grNumber?: string;
  materialDescription: string;
  inspectorName: string;
  testDate: string;
  labReference?: string;
  measurements: QCMeasurement[];
  overallStatus: 'PASS' | 'FAIL';
  notes?: string;
  createdAt: string;
}

export interface Invoice {
  id: string;
  invoiceNumber: string;
  vendorId: string;
  vendorName: string;
  invoiceDate: string;
  dueDate: string;
  totalAmount: number;
  currency: string;
  matchStatus: 'PENDING' | 'MATCHED' | 'MISMATCHED';
  validationFlags: string[];
  matchedPoId?: string;
  matchedGrId?: string;
  matchedQcId?: string;
  lineItems: {
    description: string;
    quantity: number;
    unitPrice: number;
    totalPrice: number;
  }[];
  createdAt: string;
}

// Mock Vendors
const mockVendors: Vendor[] = [
  {
    id: 'v1',
    name: 'Gulf Coast Oil Suppliers',
    contactEmail: 'orders@gulfcoastoil.com',
    contactPhone: '555-0100',
    address: 'Houston, TX',
  },
  {
    id: 'v2',
    name: 'West Texas Drilling Co.',
    contactEmail: 'procurement@wtdrilling.com',
    contactPhone: '555-0200',
    address: 'Midland, TX',
  },
  {
    id: 'v3',
    name: 'Offshore Equipment Supply',
    contactEmail: 'sales@offshoreequip.com',
    contactPhone: '555-0300',
    address: 'New Orleans, LA',
  },
  {
    id: 'v4',
    name: 'Chemical Solutions Inc.',
    contactEmail: 'orders@chemsolutions.com',
    contactPhone: '555-0400',
    address: 'Baton Rouge, LA',
  },
];

// Mock Purchase Orders
const mockPurchaseOrders: PurchaseOrder[] = [
  {
    id: 'po1',
    poNumber: 'PO-20251028-0001',
    vendorId: 'v1',
    vendorName: 'Gulf Coast Oil Suppliers',
    materialDescription: 'Crude Oil - West Texas Intermediate',
    quantity: 10000,
    unit: 'barrels',
    unitPrice: 75.50,
    totalAmount: 755000,
    deliveryDate: '2025-11-15',
    status: 'PARTIALLY_RECEIVED',
    qualitySpecs: [
      { parameter: 'API Gravity', min: 38, max: 42, unit: '°API' },
      { parameter: 'Sulfur Content', max: 0.5, unit: '% weight' },
      { parameter: 'Viscosity', min: 30, max: 35, unit: 'cSt' },
    ],
    createdAt: '2025-10-28T10:00:00Z',
  },
  {
    id: 'po2',
    poNumber: 'PO-20251027-0002',
    vendorId: 'v2',
    vendorName: 'West Texas Drilling Co.',
    materialDescription: 'Drilling Mud - Water-Based',
    quantity: 5000,
    unit: 'gallons',
    unitPrice: 12.50,
    totalAmount: 62500,
    deliveryDate: '2025-11-10',
    status: 'OPEN',
    qualitySpecs: [
      { parameter: 'Density', min: 8.5, max: 9.5, unit: 'ppg' },
      { parameter: 'Viscosity', min: 40, max: 50, unit: 'seconds' },
      { parameter: 'pH Level', min: 9, max: 10, unit: 'pH' },
    ],
    createdAt: '2025-10-27T14:30:00Z',
  },
  {
    id: 'po3',
    poNumber: 'PO-20251026-0003',
    vendorId: 'v3',
    vendorName: 'Offshore Equipment Supply',
    materialDescription: 'Drill Pipe - 5 inch Premium',
    quantity: 100,
    unit: 'joints',
    unitPrice: 1800,
    totalAmount: 180000,
    deliveryDate: '2025-11-20',
    status: 'OPEN',
    qualitySpecs: [
      { parameter: 'Wall Thickness', min: 9.5, max: 10.0, unit: 'mm' },
      { parameter: 'Tensile Strength', min: 110000, unit: 'psi' },
    ],
    createdAt: '2025-10-26T09:15:00Z',
  },
  {
    id: 'po4',
    poNumber: 'PO-20251025-0004',
    vendorId: 'v4',
    vendorName: 'Chemical Solutions Inc.',
    materialDescription: 'Corrosion Inhibitor - Type A',
    quantity: 500,
    unit: 'gallons',
    unitPrice: 45.00,
    totalAmount: 22500,
    deliveryDate: '2025-11-05',
    status: 'FULLY_RECEIVED',
    qualitySpecs: [
      { parameter: 'Active Ingredient', min: 95, unit: '% purity' },
      { parameter: 'pH Level', min: 7, max: 8, unit: 'pH' },
    ],
    createdAt: '2025-10-25T11:00:00Z',
  },
];

// Mock Goods Receipts
const mockGoodsReceipts: GoodsReceipt[] = [
  {
    id: 'gr1',
    grNumber: 'GR-20251028-0001',
    poId: 'po1',
    poNumber: 'PO-20251028-0001',
    vendorId: 'v1',
    vendorName: 'Gulf Coast Oil Suppliers',
    materialDescription: 'Crude Oil - West Texas Intermediate',
    receivedDate: '2025-10-28',
    receivedBy: 'John Smith',
    receivedQuantity: 9800,
    unit: 'barrels',
    notes: 'Partial delivery - remaining 200 barrels expected next week',
    matchStatus: 'MATCHED',
    createdAt: '2025-10-28T15:30:00Z',
  },
  {
    id: 'gr2',
    grNumber: 'GR-20251027-0002',
    poId: 'po4',
    poNumber: 'PO-20251025-0004',
    vendorId: 'v4',
    vendorName: 'Chemical Solutions Inc.',
    materialDescription: 'Corrosion Inhibitor - Type A',
    receivedDate: '2025-10-27',
    receivedBy: 'Sarah Johnson',
    receivedQuantity: 500,
    unit: 'gallons',
    documentUrl: '/mock-documents/bol-gr2.pdf',
    confidenceScores: {
      vendorName: 98,
      materialDescription: 95,
      quantity: 99,
      deliveryDate: 97,
    },
    matchStatus: 'MATCHED',
    createdAt: '2025-10-27T16:45:00Z',
  },
  {
    id: 'gr3',
    grNumber: 'GR-20251026-0003',
    poId: 'po1',
    poNumber: 'PO-20251028-0001',
    vendorId: 'v1',
    vendorName: 'Gulf Coast Oil Suppliers',
    materialDescription: 'Crude Oil - West Texas Intermediate',
    receivedDate: '2025-10-26',
    receivedBy: 'Mike Davis',
    receivedQuantity: 200,
    unit: 'barrels',
    notes: 'Initial partial delivery',
    matchStatus: 'PENDING',
    createdAt: '2025-10-26T10:20:00Z',
  },
];

// Mock QC Records
const mockQCRecords: QCRecord[] = [
  {
    id: 'qc1',
    qcNumber: 'QC-20251028-0001',
    poId: 'po1',
    poNumber: 'PO-20251028-0001',
    grId: 'gr1',
    grNumber: 'GR-20251028-0001',
    materialDescription: 'Crude Oil - West Texas Intermediate',
    inspectorName: 'Dr. Emily Chen',
    testDate: '2025-10-28',
    labReference: 'LAB-2025-1028',
    measurements: [
      {
        parameter: 'API Gravity',
        value: 40.2,
        unit: '°API',
        specification: '38-42 °API',
        status: 'PASS',
      },
      {
        parameter: 'Sulfur Content',
        value: 0.42,
        unit: '% weight',
        specification: '< 0.5 % weight',
        status: 'PASS',
      },
      {
        parameter: 'Viscosity',
        value: 32.5,
        unit: 'cSt',
        specification: '30-35 cSt',
        status: 'PASS',
      },
    ],
    overallStatus: 'PASS',
    createdAt: '2025-10-28T17:00:00Z',
  },
  {
    id: 'qc2',
    qcNumber: 'QC-20251027-0002',
    poId: 'po4',
    poNumber: 'PO-20251025-0004',
    grId: 'gr2',
    grNumber: 'GR-20251027-0002',
    materialDescription: 'Corrosion Inhibitor - Type A',
    inspectorName: 'Dr. Robert Martinez',
    testDate: '2025-10-27',
    labReference: 'LAB-2025-1027',
    measurements: [
      {
        parameter: 'Active Ingredient',
        value: 96.5,
        unit: '% purity',
        specification: '≥ 95 % purity',
        status: 'PASS',
      },
      {
        parameter: 'pH Level',
        value: 7.4,
        unit: 'pH',
        specification: '7-8 pH',
        status: 'PASS',
      },
    ],
    overallStatus: 'PASS',
    createdAt: '2025-10-27T18:30:00Z',
  },
  {
    id: 'qc3',
    qcNumber: 'QC-20251026-0003',
    poId: 'po2',
    poNumber: 'PO-20251027-0002',
    materialDescription: 'Drilling Mud - Water-Based',
    inspectorName: 'Dr. Lisa Anderson',
    testDate: '2025-10-26',
    labReference: 'LAB-2025-1026',
    measurements: [
      {
        parameter: 'Density',
        value: 9.8,
        unit: 'ppg',
        specification: '8.5-9.5 ppg',
        status: 'FAIL',
      },
      {
        parameter: 'Viscosity',
        value: 45,
        unit: 'seconds',
        specification: '40-50 seconds',
        status: 'PASS',
      },
      {
        parameter: 'pH Level',
        value: 9.5,
        unit: 'pH',
        specification: '9-10 pH',
        status: 'PASS',
      },
    ],
    overallStatus: 'FAIL',
    notes: 'Density exceeds specification - batch rejected',
    createdAt: '2025-10-26T14:15:00Z',
  },
];

// Mock Invoices
const mockInvoices: Invoice[] = [
  {
    id: 'inv1',
    invoiceNumber: 'INV-8001',
    vendorId: 'v1',
    vendorName: 'Gulf Coast Oil Suppliers',
    invoiceDate: '2025-10-28',
    dueDate: '2025-11-27',
    totalAmount: 740300,
    currency: 'USD',
    matchStatus: 'MATCHED',
    validationFlags: [],
    matchedPoId: 'po1',
    matchedGrId: 'gr1',
    matchedQcId: 'qc1',
    lineItems: [
      {
        description: 'Crude Oil - West Texas Intermediate',
        quantity: 9800,
        unitPrice: 75.50,
        totalPrice: 740300,
      },
    ],
    createdAt: '2025-10-28T18:00:00Z',
  },
  {
    id: 'inv2',
    invoiceNumber: 'INV-8002',
    vendorId: 'v4',
    vendorName: 'Chemical Solutions Inc.',
    invoiceDate: '2025-10-27',
    dueDate: '2025-11-26',
    totalAmount: 22500,
    currency: 'USD',
    matchStatus: 'MATCHED',
    validationFlags: [],
    matchedPoId: 'po4',
    matchedGrId: 'gr2',
    matchedQcId: 'qc2',
    lineItems: [
      {
        description: 'Corrosion Inhibitor - Type A',
        quantity: 500,
        unitPrice: 45.00,
        totalPrice: 22500,
      },
    ],
    createdAt: '2025-10-27T19:00:00Z',
  },
  {
    id: 'inv3',
    invoiceNumber: 'INV-7901',
    vendorId: 'v2',
    vendorName: 'West Texas Drilling Co.',
    invoiceDate: '2025-10-26',
    dueDate: '2025-11-25',
    totalAmount: 63750,
    currency: 'USD',
    matchStatus: 'MISMATCHED',
    validationFlags: ['PRICE_MISMATCH', 'MISSING_GR'],
    matchedPoId: 'po2',
    lineItems: [
      {
        description: 'Drilling Mud - Water-Based',
        quantity: 5000,
        unitPrice: 12.75, // Price mismatch: PO has 12.50
        totalPrice: 63750,
      },
    ],
    createdAt: '2025-10-26T16:30:00Z',
  },
  {
    id: 'inv4',
    invoiceNumber: 'INV-7902',
    vendorId: 'v3',
    vendorName: 'Offshore Equipment Supply',
    invoiceDate: '2025-10-25',
    dueDate: '2025-11-24',
    totalAmount: 180000,
    currency: 'USD',
    matchStatus: 'PENDING',
    validationFlags: ['MISSING_GR', 'MISSING_QC'],
    matchedPoId: 'po3',
    lineItems: [
      {
        description: 'Drill Pipe - 5 inch Premium',
        quantity: 100,
        unitPrice: 1800,
        totalPrice: 180000,
      },
    ],
    createdAt: '2025-10-25T14:00:00Z',
  },
];

// Mock Data Service
class MockDataService {
  private vendors: Vendor[] = [...mockVendors];
  private purchaseOrders: PurchaseOrder[] = [...mockPurchaseOrders];
  private goodsReceipts: GoodsReceipt[] = [...mockGoodsReceipts];
  private qcRecords: QCRecord[] = [...mockQCRecords];
  private invoices: Invoice[] = [...mockInvoices];

  // Vendors
  getVendors(): Vendor[] {
    return this.vendors;
  }

  getVendorById(id: string): Vendor | undefined {
    return this.vendors.find(v => v.id === id);
  }

  // Purchase Orders
  getPurchaseOrders(): PurchaseOrder[] {
    return this.purchaseOrders;
  }

  getPurchaseOrderById(id: string): PurchaseOrder | undefined {
    return this.purchaseOrders.find(po => po.id === id);
  }

  createPurchaseOrder(po: Omit<PurchaseOrder, 'id' | 'poNumber' | 'createdAt'>): PurchaseOrder {
    const newPO: PurchaseOrder = {
      ...po,
      id: `po${this.purchaseOrders.length + 1}`,
      poNumber: `PO-${new Date().toISOString().split('T')[0].replace(/-/g, '')}-${String(this.purchaseOrders.length + 1).padStart(4, '0')}`,
      createdAt: new Date().toISOString(),
    };
    this.purchaseOrders.push(newPO);
    return newPO;
  }

  // Goods Receipts
  getGoodsReceipts(): GoodsReceipt[] {
    return this.goodsReceipts;
  }

  getGoodsReceiptById(id: string): GoodsReceipt | undefined {
    return this.goodsReceipts.find(gr => gr.id === id);
  }

  createGoodsReceipt(gr: Omit<GoodsReceipt, 'id' | 'grNumber' | 'createdAt'>): GoodsReceipt {
    const newGR: GoodsReceipt = {
      ...gr,
      id: `gr${this.goodsReceipts.length + 1}`,
      grNumber: `GR-${new Date().toISOString().split('T')[0].replace(/-/g, '')}-${String(this.goodsReceipts.length + 1).padStart(4, '0')}`,
      createdAt: new Date().toISOString(),
    };
    this.goodsReceipts.push(newGR);
    
    // Update PO status
    const po = this.getPurchaseOrderById(gr.poId);
    if (po) {
      const totalReceived = this.goodsReceipts
        .filter(g => g.poId === po.id)
        .reduce((sum, g) => sum + g.receivedQuantity, 0);
      
      if (totalReceived >= po.quantity) {
        po.status = 'FULLY_RECEIVED';
      } else if (totalReceived > 0) {
        po.status = 'PARTIALLY_RECEIVED';
      }
    }
    
    return newGR;
  }

  // QC Records
  getQCRecords(): QCRecord[] {
    return this.qcRecords;
  }

  getQCRecordById(id: string): QCRecord | undefined {
    return this.qcRecords.find(qc => qc.id === id);
  }

  createQCRecord(qc: Omit<QCRecord, 'id' | 'qcNumber' | 'createdAt'>): QCRecord {
    const newQC: QCRecord = {
      ...qc,
      id: `qc${this.qcRecords.length + 1}`,
      qcNumber: `QC-${new Date().toISOString().split('T')[0].replace(/-/g, '')}-${String(this.qcRecords.length + 1).padStart(4, '0')}`,
      createdAt: new Date().toISOString(),
    };
    this.qcRecords.push(newQC);
    return newQC;
  }

  // Invoices
  getInvoices(): Invoice[] {
    return this.invoices;
  }

  getInvoiceById(id: string): Invoice | undefined {
    return this.invoices.find(inv => inv.id === id);
  }

  // Simulate document extraction
  simulateDocumentExtraction(_file: File): Promise<any> {
    return new Promise((resolve) => {
      setTimeout(() => {
        resolve({
          vendorName: 'Gulf Coast Oil Suppliers',
          materialDescription: 'Crude Oil - West Texas Intermediate',
          quantity: 9800,
          unit: 'barrels',
          deliveryDate: '2025-10-28',
          bolNumber: 'BOL-2025-1028-001',
          confidenceScores: {
            vendorName: 95,
            materialDescription: 92,
            quantity: 98,
            deliveryDate: 96,
            bolNumber: 88,
          },
        });
      }, 2000); // Simulate 2 second processing time
    });
  }
}

// Export singleton instance
export const mockDataService = new MockDataService();
export default mockDataService;
