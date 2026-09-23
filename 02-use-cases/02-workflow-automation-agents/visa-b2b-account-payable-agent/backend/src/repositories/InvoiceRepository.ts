import { Repository } from 'typeorm';
import { Invoice, PaymentStatus } from '../entities/Invoice';
import { getDataSource } from '../config/database';

export interface InvoiceFilters {
  status?: PaymentStatus | PaymentStatus[];
  vendorId?: string;
  hasPoMatch?: boolean;
  hasVarianceWarning?: boolean;
  startDate?: string;
  endDate?: string;
  page?: number;
  limit?: number;
  sortBy?: 'invoice_date' | 'total_amount' | 'created_at';
  sortOrder?: 'ASC' | 'DESC';
}

export interface PaginatedResult<T> {
  data: T[];
  pagination: {
    page: number;
    limit: number;
    total: number;
    totalPages: number;
  };
}

export class InvoiceRepository {
  private repository: Repository<Invoice>;

  constructor() {
    this.initialize();
  }

  private async initialize() {
    const dataSource = await getDataSource();
    this.repository = dataSource.getRepository(Invoice);
  }

  private async getRepository(): Promise<Repository<Invoice>> {
    if (!this.repository) {
      await this.initialize();
    }
    return this.repository;
  }

  async findAll(filters: InvoiceFilters = {}): Promise<PaginatedResult<Invoice>> {
    const repo = await this.getRepository();
    const page = filters.page || 1;
    const limit = Math.min(filters.limit || 10, 100);
    const skip = (page - 1) * limit;

    const queryBuilder = repo
      .createQueryBuilder('invoice')
      .leftJoinAndSelect('invoice.vendor', 'vendor')
      .leftJoinAndSelect('invoice.purchaseOrder', 'purchaseOrder')
      .leftJoinAndSelect('invoice.goodsReceipt', 'goodsReceipt');

    // Apply filters
    if (filters.status) {
      if (Array.isArray(filters.status)) {
        queryBuilder.andWhere('invoice.paymentStatus IN (:...statuses)', {
          statuses: filters.status,
        });
      } else {
        queryBuilder.andWhere('invoice.paymentStatus = :status', {
          status: filters.status,
        });
      }
    }

    if (filters.vendorId) {
      queryBuilder.andWhere('invoice.vendorId = :vendorId', {
        vendorId: filters.vendorId,
      });
    }

    if (filters.hasPoMatch !== undefined) {
      queryBuilder.andWhere('invoice.hasPoMatch = :hasPoMatch', {
        hasPoMatch: filters.hasPoMatch,
      });
    }

    if (filters.hasVarianceWarning !== undefined) {
      queryBuilder.andWhere('invoice.hasVarianceWarning = :hasVarianceWarning', {
        hasVarianceWarning: filters.hasVarianceWarning,
      });
    }

    if (filters.startDate) {
      queryBuilder.andWhere('invoice.invoiceDate >= :startDate', {
        startDate: filters.startDate,
      });
    }

    if (filters.endDate) {
      queryBuilder.andWhere('invoice.invoiceDate <= :endDate', {
        endDate: filters.endDate,
      });
    }

    // Sorting - map snake_case to camelCase entity fields
    const sortFieldMap: Record<string, string> = {
      'invoice_date': 'invoiceDate',
      'total_amount': 'totalAmount',
      'created_at': 'createdAt',
      'updated_at': 'updatedAt',
    };
    
    const sortBy = filters.sortBy || 'created_at';
    const entityField = sortFieldMap[sortBy] || sortBy;
    const sortOrder = filters.sortOrder || 'DESC';
    queryBuilder.orderBy(`invoice.${entityField}`, sortOrder);

    // Pagination
    const [data, total] = await queryBuilder.skip(skip).take(limit).getManyAndCount();

    return {
      data,
      pagination: {
        page,
        limit,
        total,
        totalPages: Math.ceil(total / limit),
      },
    };
  }

  async findById(id: string): Promise<Invoice | null> {
    const repo = await this.getRepository();
    return repo.findOne({
      where: { id },
      relations: ['vendor', 'purchaseOrder', 'purchaseOrder.items', 'goodsReceipt'],
    });
  }

  async findByInvoiceNumber(invoiceNumber: string): Promise<Invoice | null> {
    const repo = await this.getRepository();
    return repo.findOne({
      where: { invoiceNumber },
      relations: ['vendor', 'purchaseOrder', 'purchaseOrder.items', 'goodsReceipt'],
    });
  }

  async create(invoiceData: Partial<Invoice>): Promise<Invoice> {
    const repo = await this.getRepository();
    const invoice = repo.create(invoiceData);
    return repo.save(invoice);
  }

  async update(id: string, invoiceData: Partial<Invoice>): Promise<Invoice> {
    const repo = await this.getRepository();
    await repo.update(id, invoiceData);
    const updated = await this.findById(id);
    if (!updated) {
      throw new Error('Invoice not found after update');
    }
    return updated;
  }

  async delete(id: string): Promise<void> {
    const repo = await this.getRepository();
    await repo.delete(id);
  }

  async getStatistics(): Promise<any> {
    const repo = await this.getRepository();

    const [
      totalInvoices,
      pendingInvoices,
      overdueInvoices,
      totalAmount,
      pendingAmount,
      overdueAmount,
    ] = await Promise.all([
      repo.count(),
      repo.count({ where: { paymentStatus: PaymentStatus.PENDING } }),
      repo
        .createQueryBuilder('invoice')
        .where('invoice.paymentStatus = :status', { status: PaymentStatus.PENDING })
        .andWhere('invoice.dueDate < :today', { today: new Date() })
        .getCount(),
      repo
        .createQueryBuilder('invoice')
        .select('SUM(invoice.totalAmount)', 'total')
        .getRawOne()
        .then((result) => parseFloat(result?.total || '0')),
      repo
        .createQueryBuilder('invoice')
        .select('SUM(invoice.totalAmount)', 'total')
        .where('invoice.paymentStatus = :status', { status: PaymentStatus.PENDING })
        .getRawOne()
        .then((result) => parseFloat(result?.total || '0')),
      repo
        .createQueryBuilder('invoice')
        .select('SUM(invoice.totalAmount)', 'total')
        .where('invoice.paymentStatus = :status', { status: PaymentStatus.PENDING })
        .andWhere('invoice.dueDate < :today', { today: new Date() })
        .getRawOne()
        .then((result) => parseFloat(result?.total || '0')),
    ]);

    // Calculate PO match rate
    const totalWithPo = await repo.count({ where: { hasPoMatch: true } });
    const poMatchRate = totalInvoices > 0 ? (totalWithPo / totalInvoices) * 100 : 0;

    // Count variance warnings
    const varianceWarningCount = await repo.count({ where: { hasVarianceWarning: true } });

    return {
      totalInvoices,
      totalAmount,
      pendingPayments: pendingInvoices,
      pendingAmount,
      overdueInvoices,
      overdueAmount,
      poMatchRate: Math.round(poMatchRate * 10) / 10,
      varianceWarningCount,
    };
  }
}
