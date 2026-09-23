import { Request, Response } from 'express';
import { DocumentJobService } from '../services/documentJobService';

const documentJobService = new DocumentJobService();

/**
 * Get job status by ID
 * GET /api/document-jobs/:id
 */
export const getJobStatus = async (req: Request, res: Response): Promise<void> => {
  try {
    const { id } = req.params;

    const job = await documentJobService.getJob(id);

    res.json(job);
  } catch (error) {
    console.error('Error getting job status:', error);
    
    if (error instanceof Error && error.message.includes('not found')) {
      res.status(404).json({
        error: {
          code: 404,
          message: 'Job not found',
          details: error.message,
        },
      });
      return;
    }

    res.status(500).json({
      error: {
        code: 500,
        message: 'Failed to get job status',
        details: error instanceof Error ? error.message : 'Unknown error',
      },
    });
  }
};

/**
 * Update job status (internal endpoint for Lambda)
 * PATCH /api/document-jobs/:id
 */
export const updateJobStatus = async (req: Request, res: Response): Promise<void> => {
  try {
    const { id } = req.params;
    const updates = req.body;

    const job = await documentJobService.updateJob(id, updates);

    res.json(job);
  } catch (error) {
    console.error('Error updating job status:', error);
    
    if (error instanceof Error && error.message.includes('not found')) {
      res.status(404).json({
        error: {
          code: 404,
          message: 'Job not found',
          details: error.message,
        },
      });
      return;
    }

    res.status(500).json({
      error: {
        code: 500,
        message: 'Failed to update job status',
        details: error instanceof Error ? error.message : 'Unknown error',
      },
    });
  }
};

/**
 * Get recent jobs (for admin/debugging)
 * GET /api/document-jobs
 */
export const getRecentJobs = async (req: Request, res: Response): Promise<void> => {
  try {
    const limit = parseInt(req.query.limit as string) || 10;

    const jobs = await documentJobService.getRecentJobs(limit);

    res.json(jobs);
  } catch (error) {
    console.error('Error getting recent jobs:', error);
    
    res.status(500).json({
      error: {
        code: 500,
        message: 'Failed to get recent jobs',
        details: error instanceof Error ? error.message : 'Unknown error',
      },
    });
  }
};
