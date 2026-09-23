import { Router } from 'express';
import { createQCRecord, getQCRecordById, listQCRecords } from '../controllers/qcController';
import { authenticate } from '../middleware/auth';

const router = Router();

// All routes require authentication
router.use(authenticate);

// Create QC record
router.post('/', createQCRecord);

// List QC records with filters
router.get('/', listQCRecords);

// Get QC record by ID
router.get('/:id', getQCRecordById);

export default router;
