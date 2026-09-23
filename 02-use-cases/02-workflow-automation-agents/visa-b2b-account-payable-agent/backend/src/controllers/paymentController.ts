import { Request, Response } from 'express';
import { body, query, param, validationResult } from 'express-validator';
import { paymentService } from '../services/paymentService';
import { agentCoreService } from '../services/agentCoreService';

// Validation rules
export const listValidation = [
  query('page').optional().isInt({ min: 1 }).toInt(),
  query('limit').optional().isInt({ min: 1, max: 100 }).toInt(),
  query('status').optional().isString(),
  query('vendor_id').optional().isUUID(),
  query('due_date_start').optional().isISO8601(),
  query('due_date_end').optional().isISO8601(),
  query('amount_min').optional().isFloat({ min: 0 }),
  query('amount_max').optional().isFloat({ min: 0 }),
  query('overdue_only').optional().isBoolean(),
  query('search').optional().isString(),
];

export const getByIdValidation = [param('id').isUUID()];

export const downloadFileValidation = [param('id').isUUID()];

export const processPaymentValidation = [
  body('invoice_id').isUUID().withMessage('Valid invoice_id is required'),
];

// Controllers
export const list = async (req: Request, res: Response) => {
  const errors = validationResult(req);
  if (!errors.isEmpty()) {
    return res.status(400).json({ errors: errors.array() });
  }

  try {
    const page = parseInt(req.query.page as string) || 1;
    const limit = parseInt(req.query.limit as string) || 50;
    const status = req.query.status
      ? (req.query.status as string).split(',')
      : undefined;
    const vendorId = req.query.vendor_id as string | undefined;
    const dueDateStart = req.query.due_date_start as string | undefined;
    const dueDateEnd = req.query.due_date_end as string | undefined;
    const amountMin = req.query.amount_min
      ? parseFloat(req.query.amount_min as string)
      : undefined;
    const amountMax = req.query.amount_max
      ? parseFloat(req.query.amount_max as string)
      : undefined;
    const overdueOnly = req.query.overdue_only === 'true';
    const search = req.query.search as string | undefined;

    const result = await paymentService.getPayments({
      page,
      limit,
      status,
      vendorId,
      dueDateStart,
      dueDateEnd,
      amountMin,
      amountMax,
      overdueOnly,
      search,
    });

    res.json(result);
  } catch (error) {
    console.error('Error fetching payments:', error);
    console.error('Error stack:', error instanceof Error ? error.stack : 'No stack trace');
    res.status(500).json({ 
      error: 'Failed to fetch payments',
      details: error instanceof Error ? error.message : String(error)
    });
  }
};

export const getById = async (req: Request, res: Response) => {
  const errors = validationResult(req);
  if (!errors.isEmpty()) {
    return res.status(400).json({ errors: errors.array() });
  }

  try {
    const payment = await paymentService.getPaymentById(req.params.id);

    if (!payment) {
      return res.status(404).json({ error: 'Payment not found' });
    }

    res.json(payment);
  } catch (error) {
    console.error('Error fetching payment:', error);
    res.status(500).json({ error: 'Failed to fetch payment' });
  }
};

export const getSummary = async (req: Request, res: Response) => {
  try {
    const summary = await paymentService.getPaymentSummary();
    res.json(summary);
  } catch (error) {
    console.error('Error fetching payment summary:', error);
    res.status(500).json({ error: 'Failed to fetch payment summary' });
  }
};

export const getISO20022File = async (req: Request, res: Response) => {
  const errors = validationResult(req);
  if (!errors.isEmpty()) {
    return res.status(400).json({ errors: errors.array() });
  }

  try {
    const url = await paymentService.getISO20022FileUrl(req.params.id);

    if (!url) {
      return res.status(404).json({ error: 'File not found' });
    }

    res.json({ url });
  } catch (error) {
    console.error('Error getting file URL:', error);
    res.status(500).json({ error: 'Failed to get file URL' });
  }
};

/**
 * Task 6.2: Process payment for an invoice
 * Invokes Payment Agent via HTTP (Bedrock AgentCore runtime)
 */
export const processPayment = async (req: Request, res: Response) => {
  const errors = validationResult(req);
  if (!errors.isEmpty()) {
    return res.status(400).json({ errors: errors.array() });
  }

  try {
    const { invoice_id } = req.body;

    console.log(`Processing payment for invoice: ${invoice_id}`);

    // Get Payment Agent ARN from environment
    const paymentAgentArn = process.env.PAYMENT_AGENT_ARN;
    if (!paymentAgentArn) {
      console.error('PAYMENT_AGENT_ARN not configured');
      return res.status(500).json({ 
        error: 'Payment agent not configured',
        details: 'PAYMENT_AGENT_ARN environment variable is missing'
      });
    }

    // Step 1: Validate 3-way match before processing payment
    console.log('Step 1: Validating 3-way match...');
    const canPay = await paymentService.validateThreeWayMatch(invoice_id);
    
    if (!canPay.canPay) {
      console.log(`Payment blocked: ${canPay.reason}`);
      return res.status(400).json({
        error: 'Payment cannot be processed',
        reason: canPay.reason,
        warnings: canPay.warnings,
      });
    }
    
    if (canPay.warnings && canPay.warnings.length > 0) {
      console.log('Payment warnings:', canPay.warnings);
    }

    // Step 2: Get invoice data from database
    console.log('Step 2: Fetching invoice data from database...');
    const invoiceData = await paymentService.getInvoiceDataForPayment(invoice_id);
    console.log('Invoice data retrieved:', JSON.stringify(invoiceData, null, 2));

    // Step 3: Create payment record in database
    console.log('Step 3: Creating payment record...');
    const paymentId = await paymentService.createPaymentRecord(
      invoice_id,
      'pending',
      'Awaiting agent decision',
      invoiceData.amount,
      invoiceData.currency
    );
    console.log(`Payment record created: ${paymentId}`);

    // Step 4: Call Payment Agent via Bridge Lambda
    console.log('Step 4: Invoking Payment Agent...');
    const agentPayload = {
      invoice_data: invoiceData,
      payment_id: paymentId,
    };
    console.log('Agent payload:', JSON.stringify(agentPayload, null, 2));

    const agentResponse = await agentCoreService.invokeAgent(paymentAgentArn, agentPayload);
    console.log('Payment Agent response:', JSON.stringify(agentResponse, null, 2));

    // Step 5: Update payment based on agent response
    if (agentResponse.status === 'success' || agentResponse.status === 'completed') {
      console.log('Step 5: Updating payment status to completed...');
      
      await paymentService.updatePaymentStatus(paymentId, 'completed', {
        virtualCardId: agentResponse.virtual_card_id,
        transactionId: agentResponse.transaction_id,
        paymentMethod: agentResponse.payment_method,
        agentReasoning: agentResponse.reasoning,
      });

      // Step 6: Store card details if Visa B2B payment
      if (agentResponse.payment_method === 'visa_b2b' && agentResponse.card_details) {
        console.log('Step 6: Storing encrypted card details...');
        await paymentService.storeCardDetails(paymentId, agentResponse.card_details);
      }

      // Step 7: Update invoice with payment info
      console.log('Step 7: Updating invoice with payment info...');
      await paymentService.updateInvoiceWithPayment(
        invoice_id,
        paymentId,
        agentResponse.payment_method,
        agentResponse.file_key // Pass ISO20022 file key if present
      );

      console.log('Payment processing completed successfully');
      
      res.json({
        success: true,
        payment: {
          id: paymentId,
          ...agentResponse,
        },
        message: 'Payment processed successfully',
      });
    } else {
      console.log('Step 5: Payment failed, updating status...');
      
      await paymentService.updatePaymentStatus(paymentId, 'failed', {
        paymentMethod: agentResponse.payment_method,
        agentReasoning: agentResponse.reasoning,
      });

      res.status(400).json({
        success: false,
        payment: {
          id: paymentId,
          ...agentResponse,
        },
        error: agentResponse.error || 'Payment processing failed',
      });
    }

  } catch (error) {
    console.error('Error processing payment:', error);
    console.error('Error stack:', error instanceof Error ? error.stack : 'No stack trace');
    res.status(500).json({
      error: 'Failed to process payment',
      details: error instanceof Error ? error.message : String(error),
    });
  }
};

/**
 * Task 6.3: Get decrypted card details for a payment
 * Returns full card number, CVV, and expiry for authorized buyer
 */
export const getCardDetails = async (req: Request, res: Response) => {
  const errors = validationResult(req);
  if (!errors.isEmpty()) {
    return res.status(400).json({ errors: errors.array() });
  }

  try {
    const paymentId = req.params.id;

    console.log(`Getting card details for payment: ${paymentId}`);

    // Get decrypted card details from service
    const cardDetails = await paymentService.getDecryptedCardDetails(paymentId);

    if (!cardDetails) {
      return res.status(404).json({ error: 'Payment not found or no card details available' });
    }

    res.json(cardDetails);

  } catch (error) {
    console.error('Error getting card details:', error);
    console.error('Error stack:', error instanceof Error ? error.stack : 'No stack trace');
    res.status(500).json({
      error: 'Failed to get card details',
      details: error instanceof Error ? error.message : String(error),
    });
  }
};
