/**
 * Visa B2B Stub API - Main Entry Point
 * 
 * This file exports all the stub API handlers for the Visa B2B Virtual Account Payment API.
 */

// Virtual Card Requisition handler
exports.virtualCardRequisition = async (event) => {
  console.log('VirtualCardRequisition called with:', JSON.stringify(event, null, 2));
  
  const body = typeof event.body === 'string' ? JSON.parse(event.body) : event.body;
  const { messageId, buyerId, amount, currency } = body;
  
  // Generate random card details using cryptographically secure random numbers
  const crypto = require('crypto');
  const requisitionId = parseInt(crypto.randomBytes(4).toString('hex'), 16).toString();
  const accountNumber = '4' + parseInt(crypto.randomBytes(7).toString('hex'), 16).toString().slice(0, 15).padStart(15, '0');
  const now = new Date();
  const futureExpiry = new Date(now.getFullYear() + 3, now.getMonth());
  const expiryMonth = String(futureExpiry.getMonth() + 1).padStart(2, '0');
  const expirationDate = `${expiryMonth}/${futureExpiry.getFullYear()}`;
  
  return {
    statusCode: 200,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
    },
    body: JSON.stringify({
      VCardRequistionResponse: {
        messageId,
        requisitionId,
        accountNumber,
        expirationDate,
        statusCode: '00',
        statusDesc: 'Success',
      },
    }),
  };
};

// Process Payments handler
exports.processPayments = async (event) => {
  console.log('ProcessPayments called with:', JSON.stringify(event, null, 2));
  
  const body = typeof event.body === 'string' ? JSON.parse(event.body) : event.body;
  const { messageId, buyerId, amount } = body;
  
  // Generate tracking number
  const trackingNumber = Math.floor(Math.random() * 10000000000).toString();
  
  return {
    statusCode: 200,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
    },
    body: JSON.stringify({
      ProcessResponse: {
        messageId,
        trackingNumber,
        cardHolderName: 'ACME CORPORATION',
        statusCode: '00',
        statusDesc: 'Success',
      },
    }),
  };
};

// Get Payment Details handler
exports.getPaymentDetails = async (event) => {
  console.log('GetPaymentDetails called with:', JSON.stringify(event, null, 2));
  
  const body = typeof event.body === 'string' ? JSON.parse(event.body) : event.body;
  const { messageId, buyerId, trackingNumber } = body;
  
  return {
    statusCode: 200,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
    },
    body: JSON.stringify({
      GetPaymentResponse: {
        messageId,
        trackingNumber,
        statusCode: '00',
        statusDesc: 'Payment Completed Successfully',
        amount: 1000.00,
        currency: 'USD',
        paymentDate: new Date().toISOString(),
      },
    }),
  };
};

// Get Security Code (CVV2) handler
exports.getSecurityCode = async (event) => {
  const body = typeof event.body === 'string' ? JSON.parse(event.body) : event.body;
  const { messageId, accountNumber } = body;

  // Log only non-sensitive identifiers to avoid exposing card data
  console.log('GetSecurityCode called with:', JSON.stringify({ messageId }, null, 2));
  
  // Generate a random 3-digit CVV2
  const cvv2 = Math.floor(100 + Math.random() * 900).toString();
  
  return {
    statusCode: 200,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
    },
    body: JSON.stringify({
      GetSecurityCodeResponse: {
        messageId,
        cvv2,
        statusCode: '00',
        statusDesc: 'Success',
      },
    }),
  };
};

