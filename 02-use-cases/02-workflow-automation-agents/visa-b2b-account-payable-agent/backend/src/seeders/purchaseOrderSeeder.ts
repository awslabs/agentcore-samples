import { DataSource } from 'typeorm';
import { Vendor } from '../entities/Vendor';
import { PurchaseOrder } from '../entities/PurchaseOrder';
import { PurchaseOrderItem } from '../entities/PurchaseOrderItem';

export async function seedPurchaseOrders(dataSource: DataSource): Promise<void> {
    const vendorRepo = dataSource.getRepository(Vendor);
    const poRepo = dataSource.getRepository(PurchaseOrder);
    const itemRepo = dataSource.getRepository(PurchaseOrderItem);

    // Create vendors
    const vendors = await vendorRepo.save([
        {
            name: 'Acme Industrial Supplies',
            code: 'ACME-001',
            contactEmail: 'orders@acme-industrial.com',
            contactPhone: '+1-555-0100',
        },
        {
            name: 'Global Equipment Corp',
            code: 'GEC-002',
            contactEmail: 'sales@globalequip.com',
            contactPhone: '+1-555-0200',
        },
        {
            name: 'Premier Materials Ltd',
            code: 'PML-003',
            contactEmail: 'info@premiermaterials.com',
            contactPhone: '+1-555-0300',
        },
    ]);

    // Create purchase orders with items
    const pos = [
        {
            poNumber: 'PO-2025-00052',
            vendor: vendors[0],
            sourceLocation: 'Offshore-Platform-KG12',
            assignedDate: new Date('2025-07-23'),
            dueDate: new Date('2025-08-15'),
            status: 'active',
            totalAmount: 25500,
            currency: 'USD',
            warehouse: 'Warehouse A',
            items: [
                {
                    description: 'Industrial Chemical Grade A',
                    quantity: 1000,
                    receivedQuantity: 0,
                    unitPrice: 25.5,
                    totalPrice: 25500,
                },
            ],
        },
        {
            poNumber: 'PO-2025-00053',
            vendor: vendors[1],
            sourceLocation: 'Gas Processing - LA02',
            assignedDate: new Date('2025-07-24'),
            dueDate: new Date('2025-08-20'),
            status: 'active',
            totalAmount: 22500,
            currency: 'USD',
            warehouse: 'Warehouse B',
            items: [
                {
                    description: 'Processing Equipment Parts',
                    quantity: 50,
                    receivedQuantity: 0,
                    unitPrice: 450,
                    totalPrice: 22500,
                },
            ],
        },
        {
            poNumber: 'PO-2025-00054',
            vendor: vendors[2],
            sourceLocation: 'Onshore-DrillRig-Permian',
            assignedDate: new Date('2025-07-25'),
            dueDate: new Date('2025-08-10'),
            status: 'active',
            totalAmount: 31500,
            currency: 'USD',
            warehouse: 'Warehouse A',
            items: [
                {
                    description: 'Drilling Fluids',
                    quantity: 2000,
                    receivedQuantity: 0,
                    unitPrice: 15.75,
                    totalPrice: 31500,
                },
            ],
        },
        {
            poNumber: 'PO-2025-00055',
            vendor: vendors[0],
            sourceLocation: 'Refinery-TX01',
            assignedDate: new Date('2025-07-26'),
            dueDate: new Date('2025-09-01'),
            status: 'active',
            totalAmount: 42500,
            currency: 'USD',
            warehouse: 'Warehouse C',
            items: [
                {
                    description: 'Refinery Catalysts',
                    quantity: 500,
                    receivedQuantity: 0,
                    unitPrice: 85,
                    totalPrice: 42500,
                },
            ],
        },
        {
            poNumber: 'PO-2025-00056',
            vendor: vendors[1],
            sourceLocation: 'Pipeline Maint - OH',
            assignedDate: new Date('2025-07-27'),
            dueDate: new Date('2025-08-25'),
            status: 'active',
            totalAmount: 18750,
            currency: 'USD',
            warehouse: 'Warehouse B',
            items: [
                {
                    description: 'Pipeline Coating Materials',
                    quantity: 1500,
                    receivedQuantity: 0,
                    unitPrice: 12.5,
                    totalPrice: 18750,
                },
            ],
        },
    ];

    for (const poData of pos) {
        const { items, ...poFields } = poData;
        const po = await poRepo.save(poFields as any);

        for (const itemData of items) {
            await itemRepo.save({
                ...itemData,
                purchaseOrderId: po.id,
            });
        }
    }

    console.log('✅ Purchase orders seeded successfully');
}
