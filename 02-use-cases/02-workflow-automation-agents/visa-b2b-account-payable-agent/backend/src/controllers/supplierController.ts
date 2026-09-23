import { Request, Response } from 'express';
import { body, param, validationResult } from 'express-validator';
import { supplierService } from '../services/supplierService';

// Validation rules
export const loginValidation = [
  body('identifier')
    .isString()
    .notEmpty()
    .withMessage('Payment ID or Tracking Number is required'),
];

export const getPaymentValidation = [
  param('id').isUUID().withMessage('Valid payment ID is required'),
];

/**
 * Task 7.1: Supplier login endpoint
 * Accept Payment ID or Tracking Number
 * Verify payment exists and return session token
 */
export const login = async (req: Request, res: Response) => {
  const errors = validationResult(req);
  if (!errors.isEmpty()) {
    return res.status(400).json({ errors: errors.array() });
  }

  try {
    const { identifier } = req.body;

    console.log(`Supplier login attempt with identifier: ${identifier}`);

    // Verify payment exists and get session token
    const result = await supplierService.login(identifier);

    if (!result) {
      return res.status(404).json({
        error: 'Payment not found',
        message: 'No payment found with the provided Payment ID or Tracking Number',
      });
    }

    res.json({
      success: true,
      paymentId: result.paymentId,
      token: result.token,
      message: 'Login successful',
    });
  } catch (error) {
    console.error('Error during supplier login:', error);
    console.error('Error stack:', error instanceof Error ? error.stack : 'No stack trace');
    res.status(500).json({
      error: 'Login failed',
      details: error instanceof Error ? error.message : String(error),
    });
  }
};

/**
 * Task 7.2: Supplier payment details endpoint
 * Get payment with invoice details and decrypted card
 * Log supplier access
 */
export const getPaymentDetails = async (req: Request, res: Response) => {
  const errors = validationResult(req);
  if (!errors.isEmpty()) {
    return res.status(400).json({ errors: errors.array() });
  }

  try {
    const paymentId = req.params.id;
    const token = req.headers.authorization?.replace('Bearer ', '');

    if (!token) {
      return res.status(401).json({ error: 'Authorization token required' });
    }

    console.log(`Supplier accessing payment details: ${paymentId}`);

    // Get payment details with card decryption and log access
    const paymentDetails = await supplierService.getPaymentDetails(paymentId, token);

    if (!paymentDetails) {
      return res.status(404).json({ error: 'Payment not found or access denied' });
    }

    res.json(paymentDetails);
  } catch (error) {
    console.error('Error getting supplier payment details:', error);
    console.error('Error stack:', error instanceof Error ? error.stack : 'No stack trace');
    
    if (error instanceof Error && error.message === 'Invalid or expired token') {
      return res.status(401).json({ error: 'Invalid or expired token' });
    }

    res.status(500).json({
      error: 'Failed to get payment details',
      details: error instanceof Error ? error.message : String(error),
    });
  }
};

/**
 * Task 7.3: Supplier payment status endpoint
 * Get current payment status
 */
export const getPaymentStatus = async (req: Request, res: Response) => {
  const errors = validationResult(req);
  if (!errors.isEmpty()) {
    return res.status(400).json({ errors: errors.array() });
  }

  try {
    const paymentId = req.params.id;
    const token = req.headers.authorization?.replace('Bearer ', '');

    if (!token) {
      return res.status(401).json({ error: 'Authorization token required' });
    }

    console.log(`Supplier checking payment status: ${paymentId}`);

    // Get payment status
    const status = await supplierService.getPaymentStatus(paymentId, token);

    if (!status) {
      return res.status(404).json({ error: 'Payment not found or access denied' });
    }

    res.json(status);
  } catch (error) {
    console.error('Error getting payment status:', error);
    console.error('Error stack:', error instanceof Error ? error.stack : 'No stack trace');
    
    if (error instanceof Error && error.message === 'Invalid or expired token') {
      return res.status(401).json({ error: 'Invalid or expired token' });
    }

    res.status(500).json({
      error: 'Failed to get payment status',
      details: error instanceof Error ? error.message : String(error),
    });
  }
};
