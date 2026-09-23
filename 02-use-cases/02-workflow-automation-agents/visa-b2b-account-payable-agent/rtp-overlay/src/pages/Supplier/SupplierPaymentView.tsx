/**
 * Supplier Payment View Page
 * Display payment and virtual card details for supplier
 */

import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { oilGasTheme } from '../../styles/oilGasTheme';
import { VirtualCardDisplay } from '../../components/payments/VirtualCardDisplay';
import { PaymentStatusBadge } from '../../components/payments/PaymentStatusBadge';
import visaLogo from '../../assets/visa-logo.png';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'https://56hm3anw06.execute-api.us-east-1.amazonaws.com/prod';

interface PaymentData {
  payment: {
    id: string;
    amount: number;
    currency: string;
    status: string;
    payment_method: string;
    tracking_number?: string;
    created_at: string;
  };
  card_details: {
    card_number: string;
    cvv: string;
    expiry: string;
    last_four: string;
  };
  invoice: {
    invoice_number: string;
    amount: number;
    due_date: string;
    supplier_name: string;
  };
}

const SupplierPaymentView: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [paymentData, setPaymentData] = useState<PaymentData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchPaymentDetails = async () => {
      try {
        const token = sessionStorage.getItem('supplier_token');
        if (!token) {
          navigate('/supplier/login');
          return;
        }

        const response = await fetch(`${API_BASE_URL}/api/supplier/payment/${id}`, {
          headers: {
            'Authorization': `Bearer ${token}`,
          },
        });

        if (!response.ok) {
          if (response.status === 403 || response.status === 401) {
            sessionStorage.removeItem('supplier_token');
            sessionStorage.removeItem('supplier_payment_id');
            navigate('/supplier/login');
            return;
          }
          throw new Error('Failed to load payment details');
        }

        const data = await response.json();
        setPaymentData(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load payment');
      } finally {
        setLoading(false);
      }
    };

    fetchPaymentDetails();
  }, [id, navigate]);

  const handleCopyAllDetails = () => {
    if (!paymentData) return;

    const details = `
Payment Details
===============
Invoice: ${paymentData.invoice.invoice_number}
Amount: ${paymentData.payment.currency} ${paymentData.payment.amount.toFixed(2)}
Supplier: ${paymentData.invoice.supplier_name}

Virtual Card Details
====================
Card Number: ${paymentData.card_details.card_number}
CVV: ${paymentData.card_details.cvv}
Expiry: ${paymentData.card_details.expiry}

Payment ID: ${paymentData.payment.id}
${paymentData.payment.tracking_number ? `Tracking Number: ${paymentData.payment.tracking_number}` : ''}
    `.trim();

    navigator.clipboard.writeText(details);
  };

  const handleLogout = () => {
    sessionStorage.removeItem('supplier_token');
    sessionStorage.removeItem('supplier_payment_id');
    navigate('/supplier/login');
  };

  if (loading) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: oilGasTheme.colors.background,
          fontFamily: oilGasTheme.typography.fontFamily,
        }}
      >
        <div style={{ textAlign: 'center' }}>
          <div
            style={{
              fontSize: oilGasTheme.typography.fontSize.xl,
              color: oilGasTheme.colors.text,
            }}
          >
            Loading payment details...
          </div>
        </div>
      </div>
    );
  }

  if (error || !paymentData) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: oilGasTheme.colors.background,
          fontFamily: oilGasTheme.typography.fontFamily,
        }}
      >
        <div
          style={{
            backgroundColor: oilGasTheme.colors.surface,
            padding: oilGasTheme.spacing['2xl'],
            borderRadius: oilGasTheme.borderRadius.lg,
            boxShadow: oilGasTheme.shadows.lg,
            maxWidth: '500px',
            textAlign: 'center',
          }}
        >
          <div
            style={{
              fontSize: oilGasTheme.typography.fontSize['2xl'],
              fontWeight: oilGasTheme.typography.fontWeight.bold,
              color: oilGasTheme.colors.danger,
              marginBottom: oilGasTheme.spacing.md,
            }}
          >
            Error
          </div>
          <div
            style={{
              fontSize: oilGasTheme.typography.fontSize.base,
              color: oilGasTheme.colors.text,
              marginBottom: oilGasTheme.spacing.xl,
            }}
          >
            {error || 'Payment not found'}
          </div>
          <button
            onClick={() => navigate('/supplier/login')}
            style={{
              padding: `${oilGasTheme.spacing.md} ${oilGasTheme.spacing.xl}`,
              fontSize: oilGasTheme.typography.fontSize.base,
              fontWeight: oilGasTheme.typography.fontWeight.semibold,
              color: '#ffffff',
              backgroundColor: '#1A1F71',
              border: 'none',
              borderRadius: oilGasTheme.borderRadius.md,
              cursor: 'pointer',
            }}
          >
            Back to Login
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        backgroundColor: oilGasTheme.colors.background,
        fontFamily: oilGasTheme.typography.fontFamily,
        padding: `${oilGasTheme.spacing.lg} ${oilGasTheme.spacing.md}`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div style={{ maxWidth: '1100px', width: '100%' }}>
        {/* Compact Header */}
        <div
          style={{
            backgroundColor: oilGasTheme.colors.surface,
            padding: `${oilGasTheme.spacing.md} ${oilGasTheme.spacing.lg}`,
            borderRadius: oilGasTheme.borderRadius.lg,
            boxShadow: oilGasTheme.shadows.md,
            marginBottom: oilGasTheme.spacing.md,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ flex: 1 }}>
              <h1
                style={{
                  fontSize: '1.5rem',
                  fontWeight: oilGasTheme.typography.fontWeight.bold,
                  color: '#1A1F71',
                  margin: 0,
                  marginBottom: '2px',
                }}
              >
                Payment Details
              </h1>
              <p
                style={{
                  fontSize: '0.875rem',
                  color: oilGasTheme.colors.textMuted,
                  margin: 0,
                }}
              >
                Supplier: {paymentData.invoice.supplier_name}
              </p>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: oilGasTheme.spacing.md }}>
              <img 
                src={visaLogo} 
                alt="VISA" 
                style={{ 
                  height: '28px',
                  width: 'auto',
                }} 
              />
              <button
                onClick={handleLogout}
                style={{
                  padding: `6px 16px`,
                  fontSize: '0.875rem',
                  fontWeight: oilGasTheme.typography.fontWeight.medium,
                  color: oilGasTheme.colors.text,
                  backgroundColor: oilGasTheme.colors.background,
                  border: `1px solid ${oilGasTheme.colors.border}`,
                  borderRadius: oilGasTheme.borderRadius.md,
                  cursor: 'pointer',
                }}
              >
                Logout
              </button>
            </div>
          </div>
        </div>

        {/* Main Content - Two Column Layout */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr', gap: oilGasTheme.spacing.md }}>
          
          {/* Left Column - Invoice Details */}
          <div
            style={{
              backgroundColor: oilGasTheme.colors.surface,
              padding: oilGasTheme.spacing.lg,
              borderRadius: oilGasTheme.borderRadius.lg,
              boxShadow: oilGasTheme.shadows.md,
            }}
          >
            <h2
              style={{
                fontSize: '1.125rem',
                fontWeight: oilGasTheme.typography.fontWeight.bold,
                color: oilGasTheme.colors.text,
                margin: 0,
                marginBottom: oilGasTheme.spacing.md,
              }}
            >
              Invoice Information
            </h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: oilGasTheme.spacing.md }}>
              <div>
                <div
                  style={{
                    fontSize: '0.75rem',
                    color: oilGasTheme.colors.textMuted,
                    marginBottom: '4px',
                  }}
                >
                  Invoice Number
                </div>
                <div
                  style={{
                    fontSize: '1rem',
                    fontWeight: oilGasTheme.typography.fontWeight.semibold,
                    color: oilGasTheme.colors.text,
                  }}
                >
                  {paymentData.invoice.invoice_number}
                </div>
              </div>
              <div>
                <div
                  style={{
                    fontSize: '0.75rem',
                    color: oilGasTheme.colors.textMuted,
                    marginBottom: '4px',
                  }}
                >
                  Amount
                </div>
                <div
                  style={{
                    fontSize: '1.25rem',
                    fontWeight: oilGasTheme.typography.fontWeight.bold,
                    color: '#1A1F71',
                  }}
                >
                  {paymentData.payment.currency} {paymentData.payment.amount.toFixed(2)}
                </div>
              </div>
              <div>
                <div
                  style={{
                    fontSize: '0.75rem',
                    color: oilGasTheme.colors.textMuted,
                    marginBottom: '4px',
                  }}
                >
                  Due Date
                </div>
                <div
                  style={{
                    fontSize: '1rem',
                    fontWeight: oilGasTheme.typography.fontWeight.semibold,
                    color: oilGasTheme.colors.text,
                  }}
                >
                  {new Date(paymentData.invoice.due_date).toLocaleDateString()}
                </div>
              </div>
              <div>
                <div
                  style={{
                    fontSize: '0.75rem',
                    color: oilGasTheme.colors.textMuted,
                    marginBottom: '4px',
                  }}
                >
                  Payment Status
                </div>
                <div>
                  <PaymentStatusBadge 
                    status={paymentData.payment.status}
                    paymentMethod={paymentData.payment.payment_method}
                  />
                </div>
              </div>
            </div>

            {/* Next Steps - Moved to left column */}
            <div
              style={{
                backgroundColor: '#FFF9E6',
                border: '1px solid #FFC700',
                borderRadius: oilGasTheme.borderRadius.md,
                padding: oilGasTheme.spacing.md,
                marginTop: oilGasTheme.spacing.lg,
              }}
            >
              <h3
                style={{
                  fontSize: '0.875rem',
                  fontWeight: oilGasTheme.typography.fontWeight.bold,
                  color: oilGasTheme.colors.text,
                  margin: 0,
                  marginBottom: oilGasTheme.spacing.sm,
                }}
              >
                Next Steps
              </h3>
              <ol
                style={{
                  margin: 0,
                  paddingLeft: '18px',
                  fontSize: '0.8125rem',
                  color: oilGasTheme.colors.text,
                  lineHeight: '1.5',
                }}
              >
                <li style={{ marginBottom: '6px' }}>
                  Use the card details to process payment
                </li>
                <li style={{ marginBottom: '6px' }}>
                  Click "Copy All Details" for quick access
                </li>
                <li style={{ marginBottom: '6px' }}>
                  Card is valid for this amount only
                </li>
                <li>
                  Contact buyer with questions
                </li>
              </ol>
            </div>
          </div>

          {/* Right Column - Virtual Card Display */}
          <div
            style={{
              backgroundColor: oilGasTheme.colors.surface,
              padding: oilGasTheme.spacing.lg,
              borderRadius: oilGasTheme.borderRadius.lg,
              boxShadow: oilGasTheme.shadows.md,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: oilGasTheme.spacing.md }}>
              <h2
                style={{
                  fontSize: '1.125rem',
                  fontWeight: oilGasTheme.typography.fontWeight.bold,
                  color: oilGasTheme.colors.text,
                  margin: 0,
                }}
              >
                Virtual Card Details
              </h2>
              <button
                onClick={handleCopyAllDetails}
                style={{
                  padding: `6px 14px`,
                  fontSize: '0.8125rem',
                  fontWeight: oilGasTheme.typography.fontWeight.semibold,
                  color: '#ffffff',
                  backgroundColor: '#1A1F71',
                  border: 'none',
                  borderRadius: oilGasTheme.borderRadius.md,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                }}
              >
                📋 Copy All
              </button>
            </div>

            <VirtualCardDisplay
              cardNumber={paymentData.card_details.card_number}
              cvv={paymentData.card_details.cvv}
              expiry={paymentData.card_details.expiry}
              maskedCardNumber={`**** **** **** ${paymentData.card_details.last_four}`}
              amount={paymentData.payment.amount}
              currency={paymentData.payment.currency}
              paymentId={paymentData.payment.id}
              trackingNumber={paymentData.payment.tracking_number}
              paymentMethod={paymentData.payment.payment_method}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default SupplierPaymentView;
