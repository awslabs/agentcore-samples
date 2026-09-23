/**
 * Navigation Component
 * Role-based navigation menu for Oil & Gas P2P system
 */

import React, { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../../contexts/MockAuthContext';
import { oilGasTheme } from '../../styles/oilGasTheme';
import visaLogo from '../../assets/visa-logo.png';

interface NavItem {
  path: string;
  label: string;
  icon: string;
  roles: string[];
}

const navItems: NavItem[] = [
  {
    path: '/dashboard',
    label: 'Dashboard',
    icon: '📊',
    roles: ['purchasing', 'receiving', 'qc_inspector', 'treasury'],
  },
  {
    path: '/purchase-orders',
    label: 'Purchase Orders',
    icon: '📋',
    roles: ['purchasing', 'receiving', 'qc_inspector', 'treasury'],
  },
  {
    path: '/goods-receipts',
    label: 'Goods Receipts',
    icon: '📦',
    roles: ['receiving', 'treasury'],
  },
  {
    path: '/quality-control',
    label: 'Quality Control',
    icon: '🔬',
    roles: ['qc_inspector', 'treasury'],
  },
  {
    path: '/invoices',
    label: 'Invoices',
    icon: '💰',
    roles: ['treasury'],
  },
];

const Navigation: React.FC = () => {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const filteredNavItems = navItems.filter((item) =>
    user ? item.roles.includes(user.role) : false
  );

  const isActive = (path: string) => {
    return location.pathname === path || location.pathname.startsWith(path + '/');
  };

  return (
    <nav
      style={{
        backgroundColor: oilGasTheme.colors.primary,
        color: '#ffffff',
        boxShadow: oilGasTheme.shadows.md,
      }}
    >
      <div
        style={{
          maxWidth: '1400px',
          margin: '0 auto',
          padding: `${oilGasTheme.spacing.md} ${oilGasTheme.spacing.lg}`,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          {/* Logo and Brand */}
          <Link
            to="/dashboard"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: oilGasTheme.spacing.md,
              textDecoration: 'none',
              color: '#ffffff',
            }}
          >
            <img 
              src={visaLogo} 
              alt="VISA" 
              style={{ 
                height: '32px',
                width: 'auto'
              }} 
            />
            <div>
              <div
                style={{
                  fontSize: oilGasTheme.typography.fontSize.lg,
                  fontWeight: oilGasTheme.typography.fontWeight.bold,
                  lineHeight: 1.2,
                }}
              >
                Oil & Gas P2P
              </div>
              <div
                style={{
                  fontSize: oilGasTheme.typography.fontSize.xs,
                  opacity: 0.9,
                }}
              >
                Powered by VISA
              </div>
            </div>
          </Link>

          {/* Desktop Navigation */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: oilGasTheme.spacing.sm,
            }}
            className="desktop-nav"
          >
            {filteredNavItems.map((item) => (
              <Link
                key={item.path}
                to={item.path}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: oilGasTheme.spacing.sm,
                  padding: `${oilGasTheme.spacing.sm} ${oilGasTheme.spacing.md}`,
                  borderRadius: oilGasTheme.borderRadius.md,
                  textDecoration: 'none',
                  color: '#ffffff',
                  fontSize: oilGasTheme.typography.fontSize.sm,
                  fontWeight: oilGasTheme.typography.fontWeight.medium,
                  backgroundColor: isActive(item.path)
                    ? 'rgba(255, 255, 255, 0.2)'
                    : 'transparent',
                  transition: oilGasTheme.transitions.fast,
                }}
                onMouseEnter={(e) => {
                  if (!isActive(item.path)) {
                    e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.1)';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive(item.path)) {
                    e.currentTarget.style.backgroundColor = 'transparent';
                  }
                }}
              >
                <span>{item.icon}</span>
                <span>{item.label}</span>
              </Link>
            ))}
          </div>

          {/* User Menu */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: oilGasTheme.spacing.md,
            }}
          >
            {/* User Info */}
            <div
              style={{
                textAlign: 'right',
                display: 'none',
              }}
              className="user-info"
            >
              <div
                style={{
                  fontSize: oilGasTheme.typography.fontSize.sm,
                  fontWeight: oilGasTheme.typography.fontWeight.semibold,
                }}
              >
                {user?.name}
              </div>
              <div
                style={{
                  fontSize: oilGasTheme.typography.fontSize.xs,
                  opacity: 0.9,
                  textTransform: 'capitalize',
                }}
              >
                {user?.role.replace('_', ' ')}
              </div>
            </div>

            {/* Logout Button */}
            <button
              onClick={handleLogout}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: oilGasTheme.spacing.sm,
                padding: `${oilGasTheme.spacing.sm} ${oilGasTheme.spacing.md}`,
                backgroundColor: 'rgba(255, 255, 255, 0.1)',
                border: '1px solid rgba(255, 255, 255, 0.3)',
                borderRadius: oilGasTheme.borderRadius.md,
                color: '#ffffff',
                fontSize: oilGasTheme.typography.fontSize.sm,
                fontWeight: oilGasTheme.typography.fontWeight.medium,
                cursor: 'pointer',
                transition: oilGasTheme.transitions.fast,
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.2)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.1)';
              }}
            >
              <span>👤</span>
              <span>{user?.name}</span>
              <span>→</span>
            </button>

            {/* Mobile Menu Toggle */}
            <button
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              style={{
                display: 'none',
                padding: oilGasTheme.spacing.sm,
                backgroundColor: 'transparent',
                border: 'none',
                color: '#ffffff',
                fontSize: oilGasTheme.typography.fontSize.xl,
                cursor: 'pointer',
              }}
              className="mobile-menu-toggle"
            >
              {mobileMenuOpen ? '✕' : '☰'}
            </button>
          </div>
        </div>

        {/* Mobile Navigation */}
        {mobileMenuOpen && (
          <div
            style={{
              marginTop: oilGasTheme.spacing.md,
              paddingTop: oilGasTheme.spacing.md,
              borderTop: '1px solid rgba(255, 255, 255, 0.2)',
            }}
            className="mobile-nav"
          >
            {filteredNavItems.map((item) => (
              <Link
                key={item.path}
                to={item.path}
                onClick={() => setMobileMenuOpen(false)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: oilGasTheme.spacing.md,
                  padding: oilGasTheme.spacing.md,
                  borderRadius: oilGasTheme.borderRadius.md,
                  textDecoration: 'none',
                  color: '#ffffff',
                  fontSize: oilGasTheme.typography.fontSize.base,
                  fontWeight: oilGasTheme.typography.fontWeight.medium,
                  backgroundColor: isActive(item.path)
                    ? 'rgba(255, 255, 255, 0.2)'
                    : 'transparent',
                  marginBottom: oilGasTheme.spacing.sm,
                }}
              >
                <span style={{ fontSize: oilGasTheme.typography.fontSize.xl }}>{item.icon}</span>
                <span>{item.label}</span>
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* Responsive Styles */}
      <style>{`
        @media (min-width: 768px) {
          .user-info {
            display: block !important;
          }
        }
        
        @media (max-width: 768px) {
          .desktop-nav {
            display: none !important;
          }
          .mobile-menu-toggle {
            display: block !important;
          }
        }
      `}</style>
    </nav>
  );
};

export default Navigation;
