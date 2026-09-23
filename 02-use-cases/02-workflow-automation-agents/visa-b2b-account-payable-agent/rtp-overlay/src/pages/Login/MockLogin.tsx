/**
 * Mock Login Page
 * Simple role selection for demo purposes
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth, type UserRole } from '../../contexts/MockAuthContext';
import { oilGasTheme } from '../../styles/oilGasTheme';
import visaLogo from '../../assets/visa-logo.png';

const MockLogin: React.FC = () => {
  const [selectedRole, setSelectedRole] = useState<UserRole>('treasury');
  const [username, setUsername] = useState('');
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    login(selectedRole, username || undefined);
    navigate('/dashboard');
  };

  const roles: { value: UserRole; label: string; description: string }[] = [
    {
      value: 'purchasing',
      label: 'Purchasing Personnel',
      description: 'Create and manage purchase orders',
    },
    {
      value: 'receiving',
      label: 'Receiving Personnel',
      description: 'Record goods receipts and deliveries',
    },
    {
      value: 'qc_inspector',
      label: 'QC Inspector',
      description: 'Perform quality control inspections',
    },
    {
      value: 'treasury',
      label: 'Treasury Personnel',
      description: 'Review invoices and approve payments',
    },
  ];

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
          maxWidth: '700px',
          margin: oilGasTheme.spacing.md,
        }}
      >
        {/* Header */}
        <div style={{ marginBottom: oilGasTheme.spacing.lg }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: oilGasTheme.spacing.md }}>
            <div style={{ flex: 1 }}>
              <h1
                style={{
                  fontSize: oilGasTheme.typography.fontSize['3xl'],
                  fontWeight: oilGasTheme.typography.fontWeight.bold,
                  color: '#1A1F71',
                  margin: 0,
                  marginBottom: oilGasTheme.spacing.xs,
                }}
              >
                Oil & Gas P2P
              </h1>
              <p
                style={{
                  fontSize: oilGasTheme.typography.fontSize.base,
                  color: oilGasTheme.colors.textMuted,
                  margin: 0,
                }}
              >
                Procure-to-Pay Management System
              </p>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: oilGasTheme.spacing.xs }}>
              <img 
                src={visaLogo} 
                alt="VISA" 
                style={{ 
                  height: '40px',
                  width: 'auto',
                }} 
              />
              <span
                style={{
                  fontSize: oilGasTheme.typography.fontSize.xs,
                  color: '#FFC700',
                  fontWeight: oilGasTheme.typography.fontWeight.semibold,
                }}
              >
                Powered by
              </span>
            </div>
          </div>
        </div>

        {/* Demo Notice */}
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
              fontSize: oilGasTheme.typography.fontSize.base,
              color: oilGasTheme.colors.text,
              margin: 0,
            }}
          >
            <strong>Demo Mode:</strong> Select a role to explore the system. No password required.
          </p>
        </div>

        {/* Login Form */}
        <form onSubmit={handleLogin}>
          {/* Username (Optional) */}
          <div style={{ marginBottom: oilGasTheme.spacing.lg }}>
            <label
              htmlFor="username"
              style={{
                display: 'block',
                fontSize: oilGasTheme.typography.fontSize.base,
                fontWeight: oilGasTheme.typography.fontWeight.medium,
                color: oilGasTheme.colors.text,
                marginBottom: oilGasTheme.spacing.sm,
              }}
            >
              Username (Optional)
            </label>
            <input
              type="text"
              id="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Enter your name"
              style={{
                width: '100%',
                padding: oilGasTheme.spacing.md,
                fontSize: oilGasTheme.typography.fontSize.lg,
                border: `1px solid ${oilGasTheme.colors.border}`,
                borderRadius: oilGasTheme.borderRadius.md,
                outline: 'none',
                transition: oilGasTheme.transitions.fast,
                boxSizing: 'border-box',
              }}
              onFocus={(e) => {
                e.target.style.borderColor = oilGasTheme.colors.primary;
              }}
              onBlur={(e) => {
                e.target.style.borderColor = oilGasTheme.colors.border;
              }}
            />
          </div>

          {/* Role Selection */}
          <div style={{ marginBottom: oilGasTheme.spacing.xl }}>
            <label
              style={{
                display: 'block',
                fontSize: oilGasTheme.typography.fontSize.lg,
                fontWeight: oilGasTheme.typography.fontWeight.medium,
                color: oilGasTheme.colors.text,
                marginBottom: oilGasTheme.spacing.md,
              }}
            >
              Select Role
            </label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: oilGasTheme.spacing.sm }}>
              {roles.map((role) => (
                <label
                  key={role.value}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    padding: oilGasTheme.spacing.md,
                    border: `2px solid ${
                      selectedRole === role.value
                        ? oilGasTheme.colors.primary
                        : oilGasTheme.colors.border
                    }`,
                    borderRadius: oilGasTheme.borderRadius.md,
                    cursor: 'pointer',
                    transition: oilGasTheme.transitions.fast,
                    backgroundColor:
                      selectedRole === role.value
                        ? oilGasTheme.colors.primary + '10'
                        : oilGasTheme.colors.surface,
                  }}
                  onMouseEnter={(e) => {
                    if (selectedRole !== role.value) {
                      e.currentTarget.style.borderColor = oilGasTheme.colors.borderDark;
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (selectedRole !== role.value) {
                      e.currentTarget.style.borderColor = oilGasTheme.colors.border;
                    }
                  }}
                >
                  <input
                    type="radio"
                    name="role"
                    value={role.value}
                    checked={selectedRole === role.value}
                    onChange={(e) => setSelectedRole(e.target.value as UserRole)}
                    style={{
                      marginTop: '2px',
                      marginRight: oilGasTheme.spacing.md,
                      cursor: 'pointer',
                    }}
                  />
                  <div style={{ flex: 1 }}>
                    <div
                      style={{
                        fontSize: oilGasTheme.typography.fontSize.lg,
                        fontWeight: oilGasTheme.typography.fontWeight.semibold,
                        color: oilGasTheme.colors.text,
                        marginBottom: '4px',
                      }}
                    >
                      {role.label}
                    </div>
                    <div
                      style={{
                        fontSize: oilGasTheme.typography.fontSize.base,
                        color: oilGasTheme.colors.textMuted,
                      }}
                    >
                      {role.description}
                    </div>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {/* Login Button */}
          <button
            type="submit"
            style={{
              width: '100%',
              padding: oilGasTheme.spacing.md,
              fontSize: oilGasTheme.typography.fontSize.lg,
              fontWeight: oilGasTheme.typography.fontWeight.semibold,
              color: '#ffffff',
              backgroundColor: oilGasTheme.colors.primary,
              border: 'none',
              borderRadius: oilGasTheme.borderRadius.md,
              cursor: 'pointer',
              transition: oilGasTheme.transitions.fast,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = oilGasTheme.colors.primaryLight;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = oilGasTheme.colors.primary;
            }}
          >
            Enter System
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
            Demo Version - Mock Authentication
          </p>
        </div>
      </div>
    </div>
  );
};

export default MockLogin;
