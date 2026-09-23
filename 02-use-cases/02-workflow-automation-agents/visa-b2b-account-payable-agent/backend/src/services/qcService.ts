import { AppDataSource } from '../data-source';
import { QCRecord } from '../entities/QCRecord';
import { PurchaseOrder } from '../entities/PurchaseOrder';
import { GoodsReceipt } from '../entities/GoodsReceipt';

export class QCService {
  private qcRepository = AppDataSource.getRepository(QCRecord);
  private poRepository = AppDataSource.getRepository(PurchaseOrder);
  private grRepository = AppDataSource.getRepository(GoodsReceipt);

  /**
   * Generate QC number in format: QC-YYYYMMDD-XXXX
   */
  private async generateQCNumber(): Promise<string> {
    const today = new Date();
    const dateStr = today.toISOString().split('T')[0].replace(/-/g, '');
    
    // Find the highest sequence number for today
    const lastQC = await this.qcRepository
      .createQueryBuilder('qc')
      .where('qc.qcNumber LIKE :pattern', { pattern: `QC-${dateStr}-%` })
      .orderBy('qc.qcNumber', 'DESC')
      .getOne();

    let sequence = 1;
    if (lastQC) {
      const lastSequence = parseInt(lastQC.qcNumber.split('-')[2]);
      sequence = lastSequence + 1;
    }

    return `QC-${dateStr}-${sequence.toString().padStart(4, '0')}`;
  }

  /**
   * Validate measurement against specification
   */
  private validateMeasurement(
    value: number,
    spec: { min?: number; max?: number }
  ): 'PASS' | 'FAIL' {
    if (spec.min !== undefined && value < spec.min) return 'FAIL';
    if (spec.max !== undefined && value > spec.max) return 'FAIL';
    return 'PASS';
  }

  /**
   * Create QC record with auto-validation against PO specs
   */
  async createQCRecord(data: {
    purchaseOrderId: string;
    goodsReceiptId?: string;
    inspectorName: string;
    testDate: Date;
    labReference?: string;
    measurements: {
      [key: string]: {
        value: number;
        unit: string;
        specification?: {
          min?: number;
          max?: number;
        };
      };
    };
    notes?: string;
  }): Promise<QCRecord> {
    // Validate PO exists
    const po = await this.poRepository.findOne({
      where: { id: data.purchaseOrderId },
    });

    if (!po) {
      throw new Error('Purchase order not found');
    }

    // Validate GR if provided
    if (data.goodsReceiptId) {
      const gr = await this.grRepository.findOne({
        where: { id: data.goodsReceiptId },
      });

      if (!gr) {
        throw new Error('Goods receipt not found');
      }
    }

    // Auto-validate measurements and determine overall status
    const validatedMeasurements: any = {};
    let allPass = true;

    for (const [key, measurement] of Object.entries(data.measurements)) {
      const status = measurement.specification
        ? this.validateMeasurement(measurement.value, measurement.specification)
        : 'PASS';

      validatedMeasurements[key] = {
        value: measurement.value,
        unit: measurement.unit,
        status,
        specification: measurement.specification,
      };

      if (status === 'FAIL') {
        allPass = false;
      }
    }

    const overallStatus = allPass ? 'PASS' : 'FAIL';

    // Generate QC number
    const qcNumber = await this.generateQCNumber();

    // Create QC record
    const qcRecord = this.qcRepository.create({
      qcNumber,
      purchaseOrderId: data.purchaseOrderId,
      goodsReceiptId: data.goodsReceiptId,
      inspectorName: data.inspectorName,
      testDate: data.testDate,
      labReference: data.labReference,
      measurements: validatedMeasurements,
      overallStatus,
      notes: data.notes,
    });

    return await this.qcRepository.save(qcRecord);
  }

  /**
   * Find QC records by PO
   */
  async findByPO(poId: string): Promise<QCRecord[]> {
    return await this.qcRepository.find({
      where: { purchaseOrderId: poId },
      relations: ['purchaseOrder', 'goodsReceipt'],
      order: { testDate: 'DESC' },
    });
  }

  /**
   * Find QC records by GR
   */
  async findByGR(grId: string): Promise<QCRecord[]> {
    return await this.qcRepository.find({
      where: { goodsReceiptId: grId },
      relations: ['purchaseOrder', 'goodsReceipt'],
      order: { testDate: 'DESC' },
    });
  }

  /**
   * Find QC record by ID
   */
  async findById(id: string): Promise<QCRecord | null> {
    return await this.qcRepository.findOne({
      where: { id },
      relations: ['purchaseOrder', 'goodsReceipt'],
    });
  }

  /**
   * List QC records with filters
   */
  async findAll(filters: {
    poId?: string;
    grId?: string;
    status?: 'PASS' | 'FAIL';
    startDate?: string;
    endDate?: string;
    limit?: number;
    offset?: number;
  }): Promise<{ data: QCRecord[]; total: number }> {
    const query = this.qcRepository
      .createQueryBuilder('qc')
      .leftJoinAndSelect('qc.purchaseOrder', 'po')
      .leftJoinAndSelect('qc.goodsReceipt', 'gr');

    if (filters.poId) {
      query.andWhere('qc.purchaseOrderId = :poId', { poId: filters.poId });
    }

    if (filters.grId) {
      query.andWhere('qc.goodsReceiptId = :grId', { grId: filters.grId });
    }

    if (filters.status) {
      query.andWhere('qc.overallStatus = :status', { status: filters.status });
    }

    if (filters.startDate) {
      query.andWhere('qc.testDate >= :startDate', { startDate: filters.startDate });
    }

    if (filters.endDate) {
      query.andWhere('qc.testDate <= :endDate', { endDate: filters.endDate });
    }

    query.orderBy('qc.testDate', 'DESC');

    const total = await query.getCount();

    if (filters.limit) {
      query.take(filters.limit);
    }

    if (filters.offset) {
      query.skip(filters.offset);
    }

    const data = await query.getMany();

    return { data, total };
  }
}
