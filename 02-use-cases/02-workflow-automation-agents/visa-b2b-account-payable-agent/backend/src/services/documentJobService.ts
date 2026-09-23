import { Repository } from 'typeorm';
import { getDataSource } from '../config/database';
import { DocumentJob, JobType, JobStatus } from '../entities/DocumentJob';

export interface CreateJobDto {
  jobType: JobType;
  sourceFileKey: string;
}

export interface UpdateJobDto {
  status?: JobStatus;
  resultId?: string;
  errorMessage?: string;
  processingTimeMs?: number;
}

export class DocumentJobService {
  private async getRepository(): Promise<Repository<DocumentJob>> {
    const dataSource = await getDataSource();
    return dataSource.getRepository(DocumentJob);
  }

  /**
   * Create a new document processing job
   */
  async createJob(data: CreateJobDto): Promise<DocumentJob> {
    const jobRepository = await this.getRepository();
    const job = jobRepository.create({
      jobType: data.jobType,
      status: JobStatus.UPLOADED,
      sourceFileKey: data.sourceFileKey,
    });

    return await jobRepository.save(job);
  }

  /**
   * Update job status and details
   */
  async updateJob(jobId: string, updates: UpdateJobDto): Promise<DocumentJob> {
    const jobRepository = await this.getRepository();
    await jobRepository.update(jobId, {
      ...updates,
      updatedAt: new Date(),
    });

    const job = await jobRepository.findOne({ where: { id: jobId } });
    if (!job) {
      throw new Error(`Job ${jobId} not found`);
    }

    return job;
  }

  /**
   * Get job by ID
   */
  async getJob(jobId: string): Promise<DocumentJob> {
    const jobRepository = await this.getRepository();
    const job = await jobRepository.findOne({ where: { id: jobId } });
    if (!job) {
      throw new Error(`Job ${jobId} not found`);
    }

    return job;
  }

  /**
   * Mark job as processing
   */
  async markProcessing(jobId: string): Promise<void> {
    const jobRepository = await this.getRepository();
    await jobRepository.update(jobId, {
      status: JobStatus.PROCESSING,
      updatedAt: new Date(),
    });
  }

  /**
   * Mark job as completed
   */
  async markCompleted(jobId: string, resultId: string, processingTimeMs: number): Promise<void> {
    const jobRepository = await this.getRepository();
    await jobRepository.update(jobId, {
      status: JobStatus.COMPLETED,
      resultId,
      processingTimeMs,
      updatedAt: new Date(),
    });
  }

  /**
   * Mark job as failed
   */
  async markFailed(jobId: string, error: string): Promise<void> {
    const jobRepository = await this.getRepository();
    await jobRepository.update(jobId, {
      status: JobStatus.FAILED,
      errorMessage: error,
      updatedAt: new Date(),
    });
  }

  /**
   * Get jobs by status
   */
  async getJobsByStatus(status: JobStatus): Promise<DocumentJob[]> {
    const jobRepository = await this.getRepository();
    return await jobRepository.find({
      where: { status },
      order: { createdAt: 'DESC' },
    });
  }

  /**
   * Get recent jobs
   */
  async getRecentJobs(limit: number = 10): Promise<DocumentJob[]> {
    const jobRepository = await this.getRepository();
    return await jobRepository.find({
      order: { createdAt: 'DESC' },
      take: limit,
    });
  }
}
