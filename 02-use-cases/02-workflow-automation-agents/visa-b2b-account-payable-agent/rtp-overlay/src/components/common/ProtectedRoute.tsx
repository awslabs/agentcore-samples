import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import type { UserRole } from '../../types/user.types';

interface ProtectedRouteProps {
  children: React.ReactNode;
  allowedRoles?: UserRole[];
}

export const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children, allowedRoles }) => {
  const { isAuthenticated, role } = useAuth();

  // Debug logging
  console.log('ProtectedRoute Check:', {
    isAuthenticated,
    currentRole: role,
    allowedRoles,
    hasAccess: !allowedRoles || (role && allowedRoles.includes(role)),
  });

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  if (allowedRoles && role && !allowedRoles.includes(role)) {
    return (
      <div style={{ padding: '20px', textAlign: 'center' }}>
        <h2>Access Denied</h2>
        <p>You do not have permission to access this page.</p>
        <p style={{ fontSize: '12px', color: '#666', marginTop: '10px' }}>
          Your role: {role} | Required: {allowedRoles.join(', ')}
        </p>
      </div>
    );
  }

  return <>{children}</>;
};
