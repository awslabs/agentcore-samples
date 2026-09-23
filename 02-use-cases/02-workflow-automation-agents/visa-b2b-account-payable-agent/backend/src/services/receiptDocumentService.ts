import { getDataSource } from '../config/database';
import { ReceiptDocument } from '../entities/ReceiptDocument';
import { GoodsReceipt } from '../entities/GoodsReceipt';
import { S3Service } from './s3Service';
import { AppError } from '../middleware/errorHandler';
// @ts-ignore
import { v4 as uuidv4 } from 'uuid';

export class ReceiptDocumentService {
  private async getDocumentRepository() {
    const dataSource = await getDataSource();
    return dataSource.getRepository(ReceiptDocument);
  }
  
  private async getGrRepository() {
    const dataSource = await getDataSource();
    return dataSource.getRepository(GoodsReceipt);
  }
  
  private s3Service = new S3Service();

  async uploadDocuments(
    goodsReceiptId: string,
    files: Express.Multer.File[],
    uploadedById: string
  ) {
    const grRepository = await this.getGrRepository();
    const documentRepository = await this.getDocumentRepository();
    
    // Verify goods receipt exists
    const gr = await grRepository.findOne({ where: { id: goodsReceiptId } });

    if (!gr) {
      throw new AppError('Goods receipt not found', 404);
    }

    const documents: ReceiptDocument[] = [];

    for (const file of files) {
      // Generate unique S3 key
      const fileExtension = file.originalname.split('.').pop();
      const s3Key = `receipts/${goodsReceiptId}/${uuidv4()}.${fileExtension}`;

      // Upload to S3
      await this.s3Service.uploadFile(file, s3Key);

      // Save document record
      const document = documentRepository.create({
        goodsReceiptId,
        fileName: file.originalname,
        fileType: file.mimetype,
        fileSize: file.size,
        s3Key,
        s3Bucket: process.env.AWS_S3_BUCKET || '',
        uploadedById,
      });

      const saved = await documentRepository.save(document);
      documents.push(saved);
    }

    return documents;
  }

  async getDocumentUrl(documentId: string): Promise<string> {
    const documentRepository = await this.getDocumentRepository();
    const document = await documentRepository.findOne({ where: { id: documentId } });

    if (!document) {
      throw new AppError('Document not found', 404);
    }

    return this.s3Service.getSignedUrl(document.s3Key);
  }

  async deleteDocument(documentId: string): Promise<void> {
    const documentRepository = await this.getDocumentRepository();
    const document = await documentRepository.findOne({ where: { id: documentId } });

    if (!document) {
      throw new AppError('Document not found', 404);
    }

    // Delete from S3
    await this.s3Service.deleteFile(document.s3Key);

    // Delete from database
    await documentRepository.remove(document);
  }
}
