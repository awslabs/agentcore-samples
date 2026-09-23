import type { User } from '../types/user.types';

export const mockUsers: User[] = [
  {
    id: '1',
    username: 'receiving1',
    name: 'John Warehouse',
    email: 'john.warehouse@company.com',
    role: 'receiving',
  },
  {
    id: '2',
    username: 'qc1',
    name: 'Sarah Quality',
    email: 'sarah.quality@company.com',
    role: 'qc',
  },
  {
    id: '3',
    username: 'treasury1',
    name: 'Michael Finance',
    email: 'michael.finance@company.com',
    role: 'treasury',
  },
];

// Simple mock authentication - in production, this would call a real API
export const authenticateUser = (username: string, _password: string): User | null => {
  // For demo purposes, any password works
  const user = mockUsers.find((u) => u.username === username);
  return user || null;
};
