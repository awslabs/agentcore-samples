import React, { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';
import type { User, UserRole, AuthState } from '../types/user.types';
import { authService } from '../services/auth.service';

const AuthContext = createContext<AuthState | undefined>(undefined);

interface AuthProviderProps {
  children: ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  // Initialize user from localStorage if available
  const [user, setUser] = useState<User | null>(() => {
    const savedUser = localStorage.getItem('user');
    return savedUser ? JSON.parse(savedUser) : null;
  });

  const login = async (username: string, password: string) => {
    try {
      // Call real backend API
      const response = await authService.login(username, password);
      
      // Transform backend user to frontend User type
      const authenticatedUser: User = {
        id: response.user.id,
        username: response.user.username,
        email: response.user.email,
        name: response.user.name,
        role: response.user.role as UserRole,
      };
      
      setUser(authenticatedUser);
      
      // Store JWT token and user in localStorage
      localStorage.setItem('accessToken', response.accessToken);
      localStorage.setItem('user', JSON.stringify(authenticatedUser));
    } catch (error) {
      throw new Error(error instanceof Error ? error.message : 'Invalid credentials');
    }
  };

  const logout = () => {
    setUser(null);
    authService.logout();
  };

  const value: AuthState = {
    user,
    role: user?.role || null,
    isAuthenticated: !!user,
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = (): AuthState => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
