/**
 * Mock Authentication Context
 * Simple role-based authentication for demo purposes
 */

import React, { createContext, useContext, useState, useEffect } from 'react';

export type UserRole = 'purchasing' | 'receiving' | 'qc_inspector' | 'treasury';

export interface User {
  id: string;
  username: string;
  name: string;
  role: UserRole;
  email: string;
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  login: (role: UserRole, username?: string) => void;
  logout: () => void;
  hasRole: (role: UserRole) => boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const STORAGE_KEY = 'oil_gas_p2p_mock_user';

// Mock user data for each role
const mockUsers: Record<UserRole, User> = {
  purchasing: {
    id: 'user1',
    username: 'purchasing1',
    name: 'John Purchasing',
    role: 'purchasing',
    email: 'john.purchasing@oilgas.com',
  },
  receiving: {
    id: 'user2',
    username: 'receiving1',
    name: 'Sarah Receiving',
    role: 'receiving',
    email: 'sarah.receiving@oilgas.com',
  },
  qc_inspector: {
    id: 'user3',
    username: 'qc1',
    name: 'Dr. Emily QC',
    role: 'qc_inspector',
    email: 'emily.qc@oilgas.com',
  },
  treasury: {
    id: 'user4',
    username: 'treasury1',
    name: 'Michael Treasury',
    role: 'treasury',
    email: 'michael.treasury@oilgas.com',
  },
};

export const MockAuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);

  // Load user from localStorage on mount
  useEffect(() => {
    const storedUser = localStorage.getItem(STORAGE_KEY);
    if (storedUser) {
      try {
        setUser(JSON.parse(storedUser));
      } catch (error) {
        console.error('Failed to parse stored user:', error);
        localStorage.removeItem(STORAGE_KEY);
      }
    }
  }, []);

  const login = (role: UserRole, username?: string) => {
    const mockUser = mockUsers[role];
    if (username) {
      mockUser.username = username;
    }
    setUser(mockUser);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(mockUser));
  };

  const logout = () => {
    setUser(null);
    localStorage.removeItem(STORAGE_KEY);
  };

  const hasRole = (role: UserRole): boolean => {
    return user?.role === role;
  };

  const value: AuthContextType = {
    user,
    isAuthenticated: !!user,
    login,
    logout,
    hasRole,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within a MockAuthProvider');
  }
  return context;
};

export default AuthContext;
