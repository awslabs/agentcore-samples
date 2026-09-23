import { Request, Response } from 'express';
import { QCService } from '../services/qcService';
import { logger } from '../utils/logger';

const qcService = new QCService();

/**
 * Create QC record
 * POST /api/qc-records
 */
export const createQCRecord = async (req: Request, res: Response) => {
  try {
    const {
      purchaseOrderId,
      goodsReceiptId,
      inspectorName,
      testDate,
      labReference,
      measurements,
      notes,
    } = req.body;

    // Validation
    if (!purchaseOrderId || !inspectorName || !testDate || !measurements) {
      return res.status(400).json({
        message: 'Missing required fields: purchaseOrderId, inspectorName, testDate, measurements',
      });
    }

    const qcRecord = await qcService.createQCRecord({
      purchaseOrderId,
      goodsReceiptId,
      inspectorName,
      testDate: new Date(testDate),
      labReference,
      measurements,
      notes,
    });

    logger.info(`QC record created: ${qcRecord.qcNumber}`);

    res.status(201).json(qcRecord);
  } catch (error) {
    logger.error('Error creating QC record:', error);
    res.status(500).json({
      message: error instanceof Error ? error.message : 'Failed to create QC record',
    });
  }
};

/**
 * Get QC record by ID
 * GET /api/qc-records/:id
 */
export const getQCRecordById = async (req: Request, res: Response) => {
  try {
    const { id } = req.params;

    const qcRecord = await qcService.findById(id);

    if (!qcRecord) {
      return res.status(404).json({ message: 'QC record not found' });
    }

    res.json(qcRecord);
  } catch (error) {
    logger.error('Error fetching QC record:', error);
    res.status(500).json({
      message: 'Failed to fetch QC record',
    });
  }
};

/**
 * List QC records with filters
 * GET /api/qc-records
 */
export const listQCRecords = async (req: Request, res: Response) => {
  try {
    const {
      poId,
      grId,
      status,
      startDate,
      endDate,
      limit = '50',
      offset = '0',
    } = req.query;

    const result = await qcService.findAll({
      poId: poId as string,
      grId: grId as string,
      status: status as 'PASS' | 'FAIL',
      startDate: startDate as string,
      endDate: endDate as string,
      limit: parseInt(limit as string),
      offset: parseInt(offset as string),
    });

    res.json({
      data: result.data,
      pagination: {
        total: result.total,
        limit: parseInt(limit as string),
        offset: parseInt(offset as string),
      },
    });
  } catch (error) {
    logger.error('Error listing QC records:', error);
    res.status(500).json({
      message: 'Failed to list QC records',
    });
  }
};
