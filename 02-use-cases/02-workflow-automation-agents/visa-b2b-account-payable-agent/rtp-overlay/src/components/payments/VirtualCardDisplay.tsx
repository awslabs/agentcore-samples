import React, { useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  IconButton,
  Tooltip,
  Alert,
  Chip,
} from '@mui/material';
import {
  Visibility as VisibilityIcon,
  VisibilityOff as VisibilityOffIcon,
  ContentCopy as ContentCopyIcon,
  CreditCard as CreditCardIcon,
} from '@mui/icons-material';

interface VirtualCardDisplayProps {
  cardNumber: string;
  cvv: string;
  expiry: string;
  maskedCardNumber?: string;
  amount: number;
  currency: string;
  paymentId: string;
  trackingNumber?: string;
  paymentMethod?: string;
}

export const VirtualCardDisplay: React.FC<VirtualCardDisplayProps> = ({
  cardNumber,
  cvv,
  expiry,
  maskedCardNumber,
  amount,
  currency,
  paymentId,
  trackingNumber,
  paymentMethod = 'visa_b2b',
}) => {
  const [showCardNumber, setShowCardNumber] = useState(false);
  const [showCvv, setShowCvv] = useState(false);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  const handleCopy = (text: string, fieldName: string) => {
    navigator.clipboard.writeText(text);
    setCopiedField(fieldName);
    setTimeout(() => setCopiedField(null), 2000);
  };

  const handleCopyAll = () => {
    const allDetails = `
Virtual Card Details
--------------------
Card Number: ${cardNumber}
CVV: ${cvv}
Expiry: ${expiry}
Amount: ${formatCurrency(amount, currency)}
Payment ID: ${paymentId}
${trackingNumber ? `Tracking Number: ${trackingNumber}` : ''}
    `.trim();
    
    navigator.clipboard.writeText(allDetails);
    setCopiedField('all');
    setTimeout(() => setCopiedField(null), 2000);
  };

  const formatCurrency = (value: number, curr: string): string => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: curr,
    }).format(value);
  };

  const formatCardNumber = (number: string): string => {
    // Format as: 1234 5678 9012 3456
    return number.replace(/(\d{4})(?=\d)/g, '$1 ');
  };

  const displayCardNumber = showCardNumber
    ? formatCardNumber(cardNumber)
    : maskedCardNumber || '**** **** **** ' + cardNumber.slice(-4);

  return (
    <Box sx={{ mt: 2 }}>
      <Alert severity="success" sx={{ mb: 2, py: 1 }}>
        <Typography variant="body2" fontWeight={600} sx={{ fontSize: '0.9375rem' }}>
          Virtual Card Issued Successfully
        </Typography>
        <Typography variant="body2" sx={{ fontSize: '0.875rem' }}>
          {paymentMethod === 'visa_b2b' 
            ? 'Payment processed via Visa B2B Virtual Card'
            : 'Payment processed via ISO20022'}
        </Typography>
      </Alert>

      {/* Split Pane Layout */}
      <Box sx={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 2 }}>
        
        {/* Left: Virtual Card */}
        <Card
          sx={{
            background: 'linear-gradient(135deg, #1A1F71 0%, #00308F 100%)',
            color: 'white',
            position: 'relative',
            overflow: 'hidden',
          }}
        >
          <CardContent sx={{ p: 2.5, '&:last-child': { pb: 2.5 } }}>
            {/* Visa Logo Area */}
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2.5 }}>
              <Box>
                <Typography variant="body2" sx={{ opacity: 0.8, fontSize: '0.875rem' }}>
                  Virtual Card
                </Typography>
                <Chip
                  label={paymentMethod === 'visa_b2b' ? 'Visa B2B' : 'ISO20022'}
                  size="small"
                  sx={{
                    ml: 1,
                    backgroundColor: 'rgba(255, 255, 255, 0.2)',
                    color: 'white',
                    height: '22px',
                    fontSize: '0.8125rem',
                  }}
                />
              </Box>
              <CreditCardIcon sx={{ fontSize: 40, opacity: 0.3 }} />
            </Box>

            {/* Card Number */}
            <Box sx={{ mb: 2.5 }}>
              <Typography variant="body2" sx={{ opacity: 0.8, mb: 0.5, display: 'block', fontSize: '0.875rem' }}>
                Card Number
              </Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Typography
                  variant="h5"
                  sx={{
                    fontFamily: 'monospace',
                    letterSpacing: 2,
                    fontWeight: 500,
                    fontSize: '1.375rem',
                  }}
                >
                  {displayCardNumber}
                </Typography>
                <Tooltip title={showCardNumber ? 'Hide' : 'Show'}>
                  <IconButton
                    size="small"
                    onClick={() => setShowCardNumber(!showCardNumber)}
                    sx={{ color: 'white', p: 0.5 }}
                  >
                    {showCardNumber ? <VisibilityOffIcon /> : <VisibilityIcon />}
                  </IconButton>
                </Tooltip>
                <Tooltip title={copiedField === 'cardNumber' ? 'Copied!' : 'Copy'}>
                  <IconButton
                    size="small"
                    onClick={() => handleCopy(cardNumber, 'cardNumber')}
                    sx={{ color: 'white', p: 0.5 }}
                  >
                    <ContentCopyIcon />
                  </IconButton>
                </Tooltip>
              </Box>
            </Box>

            {/* CVV and Expiry */}
            <Box sx={{ display: 'flex', gap: 4 }}>
              <Box sx={{ flex: 1 }}>
                <Typography variant="body2" sx={{ opacity: 0.8, mb: 0.5, display: 'block', fontSize: '0.875rem' }}>
                  CVV
                </Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <Typography
                    variant="h6"
                    sx={{
                      fontFamily: 'monospace',
                      letterSpacing: 2,
                      fontSize: '1.125rem',
                    }}
                  >
                    {showCvv ? cvv : '***'}
                  </Typography>
                  <Tooltip title={showCvv ? 'Hide' : 'Show'}>
                    <IconButton
                      size="small"
                      onClick={() => setShowCvv(!showCvv)}
                      sx={{ color: 'white', p: 0.5 }}
                    >
                      {showCvv ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                    </IconButton>
                  </Tooltip>
                  <Tooltip title={copiedField === 'cvv' ? 'Copied!' : 'Copy'}>
                    <IconButton
                      size="small"
                      onClick={() => handleCopy(cvv, 'cvv')}
                      sx={{ color: 'white', p: 0.5 }}
                    >
                      <ContentCopyIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Box>
              </Box>

              <Box sx={{ flex: 1 }}>
                <Typography variant="body2" sx={{ opacity: 0.8, mb: 0.5, display: 'block', fontSize: '0.875rem' }}>
                  Expiry
                </Typography>
                <Typography
                  variant="h6"
                  sx={{
                    fontFamily: 'monospace',
                    letterSpacing: 1,
                    fontSize: '1.125rem',
                  }}
                >
                  {expiry}
                </Typography>
              </Box>
            </Box>

            {/* Decorative circles */}
            <Box
              sx={{
                position: 'absolute',
                bottom: -40,
                right: -40,
                width: 140,
                height: 140,
                borderRadius: '50%',
                backgroundColor: 'rgba(255, 255, 255, 0.05)',
              }}
            />
            <Box
              sx={{
                position: 'absolute',
                top: -20,
                right: 90,
                width: 70,
                height: 70,
                borderRadius: '50%',
                backgroundColor: 'rgba(255, 255, 255, 0.05)',
              }}
            />
          </CardContent>
        </Card>

        {/* Right: Payment Details */}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Card>
            <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
              <Typography variant="subtitle1" color="text.secondary" gutterBottom sx={{ fontSize: '0.9375rem', fontWeight: 600, mb: 1.5 }}>
                Payment Details
              </Typography>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                <Box>
                  <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem', mb: 0.25 }}>
                    Amount
                  </Typography>
                  <Typography variant="h6" fontWeight={600} sx={{ fontSize: '1.125rem' }}>
                    {formatCurrency(amount, currency)}
                  </Typography>
                </Box>
                <Box>
                  <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem', mb: 0.25 }}>
                    Payment ID
                  </Typography>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.8125rem' }}>
                      {paymentId.slice(0, 10)}...
                    </Typography>
                    <Tooltip title={copiedField === 'paymentId' ? 'Copied!' : 'Copy'}>
                      <IconButton
                        size="small"
                        onClick={() => handleCopy(paymentId, 'paymentId')}
                        sx={{ p: 0.5 }}
                      >
                        <ContentCopyIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </Box>
                </Box>
                {trackingNumber && (
                  <Box>
                    <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem', mb: 0.25 }}>
                      Tracking Number
                    </Typography>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                      <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.8125rem' }}>
                        {trackingNumber}
                      </Typography>
                      <Tooltip title={copiedField === 'trackingNumber' ? 'Copied!' : 'Copy'}>
                        <IconButton
                          size="small"
                          onClick={() => handleCopy(trackingNumber, 'trackingNumber')}
                          sx={{ p: 0.5 }}
                        >
                          <ContentCopyIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </Box>
                  </Box>
                )}
              </Box>
            </CardContent>
          </Card>

          {/* Copy All Button */}
          <Button
            variant="outlined"
            fullWidth
            startIcon={<ContentCopyIcon />}
            onClick={handleCopyAll}
            sx={{ py: 1, fontSize: '0.875rem' }}
          >
            {copiedField === 'all' ? 'Copied All!' : 'Copy All Details'}
          </Button>
        </Box>
      </Box>

      {/* Info Alert */}
      <Alert severity="info" sx={{ mt: 2, py: 1 }}>
        <Typography variant="body2" sx={{ fontSize: '0.875rem' }}>
          <strong>For Supplier:</strong> Share the Payment ID or Tracking Number with your supplier so they can access the card details through the supplier portal.
        </Typography>
      </Alert>
    </Box>
  );
};
