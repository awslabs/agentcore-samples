import React, { useState } from 'react';
import {
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Typography,
  Box,
  IconButton,
  useMediaQuery,
  useTheme,
  Divider,
} from '@mui/material';
import { styled } from '@mui/material/styles';
import MenuIcon from '@mui/icons-material/Menu';
import LogoutIcon from '@mui/icons-material/Logout';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../../contexts/AuthContext';
import { colors } from '../../../theme/colors';

interface NavItem {
  label: string;
  path: string;
  icon: React.ReactNode;
}

interface NavSection {
  title: string;
  items: NavItem[];
}

interface QBSidebarProps {
  sections: NavSection[];
}

const DRAWER_WIDTH = 240;

const StyledDrawer = styled(Drawer)({
  width: DRAWER_WIDTH,
  flexShrink: 0,
  '& .MuiDrawer-paper': {
    width: DRAWER_WIDTH,
    boxSizing: 'border-box',
    backgroundColor: colors.background.paper,
    borderRight: `1px solid ${colors.border.light}`,
    display: 'flex',
    flexDirection: 'column',
    position: 'fixed',
    top: 0,
    left: 0,
    height: '100vh',
  },
});

const Logo = styled(Box)({
  padding: '24px 20px',
  borderBottom: `1px solid ${colors.border.light}`,
});

const SectionTitle = styled(Typography)({
  fontSize: '12px',
  fontWeight: 600,
  color: colors.text.secondary,
  textTransform: 'uppercase',
  padding: '16px 20px 8px',
  letterSpacing: '0.5px',
});

const StyledListItemButton = styled(ListItemButton, {
  shouldForwardProp: (prop) => prop !== 'active',
})<{ active: boolean }>(({ active }) => ({
  padding: '10px 20px',
  margin: '2px 8px',
  borderRadius: '4px',
  borderLeft: active ? `3px solid ${colors.primary.main}` : '3px solid transparent',
  backgroundColor: active ? colors.primary.hover : 'transparent',
  '&:hover': {
    backgroundColor: active ? colors.primary.hover : colors.gray[50],
  },
}));

const StyledListItemIcon = styled(ListItemIcon, {
  shouldForwardProp: (prop) => prop !== 'active',
})<{ active: boolean }>(({ active }) => ({
  minWidth: '40px',
  color: active ? colors.primary.main : colors.text.secondary,
}));

const StyledListItemText = styled(ListItemText, {
  shouldForwardProp: (prop) => prop !== 'active',
})<{ active: boolean }>(({ active }) => ({
  '& .MuiListItemText-primary': {
    fontSize: '14px',
    fontWeight: active ? 600 : 400,
    color: active ? colors.primary.main : colors.text.primary,
  },
}));

export const QBSidebar: React.FC<QBSidebarProps> = ({ sections }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { logout } = useAuth();
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('md'));
  const [mobileOpen, setMobileOpen] = useState(false);

  const handleDrawerToggle = () => {
    setMobileOpen(!mobileOpen);
  };

  const handleNavigation = (path: string) => {
    navigate(path);
    if (isMobile) {
      setMobileOpen(false);
    }
  };

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const drawerContent = (
    <>
      <Logo>
        <Typography
          variant="h2"
          sx={{
            fontSize: '24px',
            fontWeight: 700,
            color: colors.primary.main,
          }}
        >
          ProcureIQ
        </Typography>
        <Typography
          variant="caption"
          sx={{
            fontSize: '12px',
            color: colors.text.secondary,
          }}
        >
          Procurement Management
        </Typography>
      </Logo>

      {sections.map((section, index) => (
        <Box key={index}>
          <SectionTitle>{section.title}</SectionTitle>
          <List>
            {section.items.map((item) => {
              const isActive = location.pathname === item.path;
              return (
                <ListItem key={item.path} disablePadding>
                  <StyledListItemButton
                    active={isActive}
                    onClick={() => handleNavigation(item.path)}
                  >
                    <StyledListItemIcon active={isActive}>
                      {item.icon}
                    </StyledListItemIcon>
                    <StyledListItemText active={isActive} primary={item.label} />
                  </StyledListItemButton>
                </ListItem>
              );
            })}
          </List>
        </Box>
      ))}

      <Box sx={{ flexGrow: 1 }} />
      <Divider sx={{ margin: '16px 0' }} />
      <List>
        <ListItem disablePadding>
          <StyledListItemButton active={false} onClick={handleLogout}>
            <StyledListItemIcon active={false}>
              <LogoutIcon />
            </StyledListItemIcon>
            <StyledListItemText active={false} primary="Logout" />
          </StyledListItemButton>
        </ListItem>
      </List>
    </>
  );

  return (
    <>
      {isMobile && (
        <IconButton
          color="inherit"
          aria-label="open drawer"
          edge="start"
          onClick={handleDrawerToggle}
          sx={{ position: 'fixed', top: 16, left: 16, zIndex: 1300 }}
        >
          <MenuIcon />
        </IconButton>
      )}

      {isMobile ? (
        <StyledDrawer
          variant="temporary"
          open={mobileOpen}
          onClose={handleDrawerToggle}
          ModalProps={{
            keepMounted: true,
          }}
        >
          {drawerContent}
        </StyledDrawer>
      ) : (
        <StyledDrawer variant="permanent" anchor="left" open>
          {drawerContent}
        </StyledDrawer>
      )}
    </>
  );
};
