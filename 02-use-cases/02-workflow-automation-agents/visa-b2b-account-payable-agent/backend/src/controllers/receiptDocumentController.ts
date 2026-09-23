import { Response, NextFunction } from 'express';
import { ReceiptDocumentService } from '../services/receiptDocumentService';
import { AuthRequest } from '../middleware/auth';
import { param, validationResult } from 'express-validator';
import { AppError } from '../middleware/errorHandler';

const documentService = new ReceiptDocumentService();

export const uploadValidation = [param('id').isUUID()];

export const upload = async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const errors = validationResult(req);
    if (!errors.isEmpty()) {
      throw new AppError('Validation failed', 400);
    }

    if (!req.user) {
      throw new AppError('Authentication required', 401);
    }

    if (!req.files || !Array.isArray(req.files) || req.files.length === 0) {
      throw new AppError('No files uploaded', 400);
    }

    const { id } = req.params;
    const files = req.files as Express.Multer.File[];

    const documents = await documentService.uploadDocuments(id, files, req.user.userId);

    res.status(201).json({
      message: 'Files uploaded successfully',
      documents: documents.map((doc) => ({
        id: doc.id,
        fileName: doc.fileName,
        fileType: doc.fileType,
        fileSize: doc.fileSize,
        uploadedAt: doc.createdAt,
      })),
    });
  } catch (error) {
    next(error);
  }
};

export const getDocumentUrl = async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const { documentId } = req.params;

    const url = await documentService.getDocumentUrl(documentId);

    res.json({ url });
  } catch (error) {
    next(error);
  }
};
