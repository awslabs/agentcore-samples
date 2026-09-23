import React from 'react';
import {
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  Box,
} from '@mui/material';
import {
  Dashboard,
  Inventory,
  Science,
  Receipt,
  Payment,
  Assessment,
  Description,
  ShoppingCart,
} from '@mui/icons-material';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import type { UserRole } from '../../types/user.types';

const drawerWidth = 240;

interface MenuItem {
  text: string;
  icon: React.ReactElement;
  path: string;
  roles: UserRole[];
}

const menuItems: MenuItem[] = [
  {
    text: 'Goods Receipt',
    icon: <Inventory />,
    path: '/delivery',
    roles: ['receiving'],
  },
  {
    text: 'Quality Control',
    icon: <Science />,
    path: '/qc',
    roles: ['qc'],
  },
  {
    text: 'Dashboard',
    icon: <Dashboard />,
    path: '/treasury',
    roles: ['treasury'],
  },
  {
    text: 'Purchase Orders',
    icon: <ShoppingCart />,
    path: '/delivery/purchase-orders',
    roles: ['treasury', 'receiving'],
  },
  {
    text: 'Invoices',
    icon: <Description />,
    path: '/invoices',
    roles: ['treasury'],
  },
  {
    text: 'Invoice Review',
    icon: <Receipt />,
    path: '/treasury/invoices',
    roles: ['treasury'],
  },
  {
    text: 'Payments Hub',
    icon: <Payment />,
    path: '/treasury/payments',
    roles: ['treasury'],
  },
  {
    text: 'Vendor Performance',
    icon: <Assessment />,
    path: '/treasury/vendors',
    roles: ['treasury'],
  },
];

interface SidebarProps {
  mobileOpen: boolean;
  onMobileClose: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ mobileOpen, onMobileClose }) => {
  const { role } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const filteredMenuItems = menuItems.filter((item) => role && item.roles.includes(role));

  const handleNavigation = (path: string) => {
    navigate(path);
    onMobileClose();
  };

  const drawerContent = (
    <Box>
      <Toolbar />
      <List>
        {filteredMenuItems.map((item) => (
          <ListItem key={item.text} disablePadding>
            <ListItemButton
              selected={location.pathname === item.path}
              onClick={() => handleNavigation(item.path)}
            >
              <ListItemIcon>{item.icon}</ListItemIcon>
              <ListItemText primary={item.text} />
            </ListItemButton>
          </ListItem>
        ))}
      </List>
    </Box>
  );

  return (
    <>
      {/* Mobile drawer */}
      <Drawer
        variant="temporary"
        open={mobileOpen}
        onClose={onMobileClose}
        ModalProps={{
          keepMounted: true, // Better open performance on mobile
        }}
        sx={{
          display: { xs: 'block', sm: 'none' },
          '& .MuiDrawer-paper': { boxSizing: 'border-box', width: drawerWidth },
        }}
      >
        {drawerContent}
      </Drawer>

      {/* Desktop drawer */}
      <Drawer
        variant="permanent"
        sx={{
          display: { xs: 'none', sm: 'block' },
          '& .MuiDrawer-paper': { boxSizing: 'border-box', width: drawerWidth },
        }}
        open
      >
        {drawerContent}
      </Drawer>
    </>
  );
};
