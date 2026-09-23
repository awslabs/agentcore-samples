/**
 * Supplier Login Page
 * Simple login with Payment ID or Tracking Number
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { oilGasTheme } from '../../styles/oilGasTheme';
import visaLogo from '../../assets/visa-logo.png';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'https://56hm3anw06.execute-api.us-east-1.amazonaws.com/prod';

const SupplierLogin: React.FC = () => {
  const [paymentIdentifier, setPaymentIdentifier] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      // Call backend supplier login endpoint
      const response = await fetch(`${API_BASE_URL}/api/supplier/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          identifier: paymentIdentifier,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || 'Payment not found');
      }

      const data = await response.json();

      // Store session token
      sessionStorage.setItem('supplier_token', data.token);
      sessionStorage.setItem('supplier_payment_id', data.paymentId);

      // Navigate to payment view
      navigate(`/supplier/payment/${data.paymentId}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  };

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
          width: '100%',
          maxWidth: '500px',
          margin: oilGasTheme.spacing.md,
        }}
      >
        {/* Header */}
        <div style={{ marginBottom: oilGasTheme.spacing.lg }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: oilGasTheme.spacing.md }}>
            <div style={{ flex: 1 }}>
              <h1
                style={{
                  fontSize: oilGasTheme.typography.fontSize['2xl'],
                  fontWeight: oilGasTheme.typography.fontWeight.bold,
                  color: '#1A1F71',
                  margin: 0,
                  marginBottom: oilGasTheme.spacing.xs,
                }}
              >
                Supplier Portal
              </h1>
              <p
                style={{
                  fontSize: oilGasTheme.typography.fontSize.base,
                  color: oilGasTheme.colors.textMuted,
                  margin: 0,
                }}
              >
                Access Your Virtual Card Payment
              </p>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: oilGasTheme.spacing.xs }}>
              <img 
                src={visaLogo} 
                alt="VISA" 
                style={{ 
                  height: '35px',
                  width: 'auto',
                }} 
              />
              <span
                style={{
                  fontSize: oilGasTheme.typography.fontSize.xs,
                  color: '#1A1F71',
                  fontWeight: oilGasTheme.typography.fontWeight.semibold,
                }}
              >
                B2B Payments
              </span>
            </div>
          </div>
        </div>

        {/* Info Notice */}
        <div
          style={{
            backgroundColor: oilGasTheme.colors.info + '20',
            border: `1px solid ${oilGasTheme.colors.info}`,
            borderRadius: oilGasTheme.borderRadius.md,
            padding: oilGasTheme.spacing.md,
            marginBottom: oilGasTheme.spacing.lg,
          }}
        >
          <p
            style={{
              fontSize: oilGasTheme.typography.fontSize.sm,
              color: oilGasTheme.colors.text,
              margin: 0,
            }}
          >
            Enter your <strong>Payment ID</strong> or <strong>Tracking Number</strong> to access your virtual card details.
          </p>
        </div>

        {/* Error Message */}
        {error && (
          <div
            style={{
              backgroundColor: oilGasTheme.colors.danger + '20',
              border: `1px solid ${oilGasTheme.colors.danger}`,
              borderRadius: oilGasTheme.borderRadius.md,
              padding: oilGasTheme.spacing.md,
              marginBottom: oilGasTheme.spacing.lg,
            }}
          >
            <p
              style={{
                fontSize: oilGasTheme.typography.fontSize.sm,
                color: oilGasTheme.colors.danger,
                margin: 0,
              }}
            >
              {error}
            </p>
          </div>
        )}

        {/* Login Form */}
        <form onSubmit={handleLogin}>
          <div style={{ marginBottom: oilGasTheme.spacing.xl }}>
            <label
              htmlFor="paymentIdentifier"
              style={{
                display: 'block',
                fontSize: oilGasTheme.typography.fontSize.base,
                fontWeight: oilGasTheme.typography.fontWeight.medium,
                color: oilGasTheme.colors.text,
                marginBottom: oilGasTheme.spacing.sm,
              }}
            >
              Payment ID or Tracking Number
            </label>
            <input
              type="text"
              id="paymentIdentifier"
              value={paymentIdentifier}
              onChange={(e) => setPaymentIdentifier(e.target.value)}
              placeholder="e.g., PAY-123456 or TRK-789012"
              required
              disabled={loading}
              style={{
                width: '100%',
                padding: oilGasTheme.spacing.md,
                fontSize: oilGasTheme.typography.fontSize.lg,
                border: `1px solid ${oilGasTheme.colors.border}`,
                borderRadius: oilGasTheme.borderRadius.md,
                outline: 'none',
                transition: oilGasTheme.transitions.fast,
                boxSizing: 'border-box',
                backgroundColor: loading ? oilGasTheme.colors.background : oilGasTheme.colors.surface,
              }}
              onFocus={(e) => {
                if (!loading) {
                  e.target.style.borderColor = oilGasTheme.colors.primary;
                }
              }}
              onBlur={(e) => {
                e.target.style.borderColor = oilGasTheme.colors.border;
              }}
            />
          </div>

          {/* Login Button */}
          <button
            type="submit"
            disabled={loading || !paymentIdentifier.trim()}
            style={{
              width: '100%',
              padding: oilGasTheme.spacing.md,
              fontSize: oilGasTheme.typography.fontSize.lg,
              fontWeight: oilGasTheme.typography.fontWeight.semibold,
              color: '#ffffff',
              backgroundColor: loading || !paymentIdentifier.trim() 
                ? oilGasTheme.colors.borderDark 
                : '#1A1F71',
              border: 'none',
              borderRadius: oilGasTheme.borderRadius.md,
              cursor: loading || !paymentIdentifier.trim() ? 'not-allowed' : 'pointer',
              transition: oilGasTheme.transitions.fast,
            }}
            onMouseEnter={(e) => {
              if (!loading && paymentIdentifier.trim()) {
                e.currentTarget.style.backgroundColor = '#2A2F81';
              }
            }}
            onMouseLeave={(e) => {
              if (!loading && paymentIdentifier.trim()) {
                e.currentTarget.style.backgroundColor = '#1A1F71';
              }
            }}
          >
            {loading ? 'Accessing...' : 'Access Payment'}
          </button>
        </form>

        {/* Footer */}
        <div
          style={{
            marginTop: oilGasTheme.spacing.xl,
            paddingTop: oilGasTheme.spacing.lg,
            borderTop: `1px solid ${oilGasTheme.colors.border}`,
            textAlign: 'center',
          }}
        >
          <p
            style={{
              fontSize: oilGasTheme.typography.fontSize.xs,
              color: oilGasTheme.colors.textMuted,
              margin: 0,
            }}
          >
            Secure Payment Portal - Visa B2B Virtual Account
          </p>
        </div>
      </div>
    </div>
  );
};

export default SupplierLogin;
