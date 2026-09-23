import { Router } from 'express';
import { getJobStatus, updateJobStatus, getRecentJobs } from '../controllers/documentJobController';
import { authenticate } from '../middleware/auth';

const router = Router();

// Get job status (public - no auth required for polling)
router.get('/:id', getJobStatus);

// Update job status (internal, requires API key - will add middleware later)
router.patch('/:id', updateJobStatus);

// Get recent jobs (authenticated users)
router.get('/', authenticate, getRecentJobs);

export default router;
