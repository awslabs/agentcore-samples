import React, { useState } from 'react';
import {
  IconButton,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Divider,
} from '@mui/material';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import UploadIcon from '@mui/icons-material/Upload';
import ReceiptIcon from '@mui/icons-material/Receipt';
import DescriptionIcon from '@mui/icons-material/Description';
import LinkIcon from '@mui/icons-material/Link';
import CloseIcon from '@mui/icons-material/Close';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import type { PurchaseOrder } from '../../types/data.types';

interface POActionMenuProps {
  purchaseOrder: PurchaseOrder;
  currentUserRole?: 'admin' | 'user';
  onUploadReceipt: (po: PurchaseOrder) => void;
  onUploadInvoice: (po: PurchaseOrder) => void;
  onViewPO: (po: PurchaseOrder) => void;
  onViewMatches: (po: PurchaseOrder) => void;
  onClosePO: (po: PurchaseOrder) => void;
  onOverrideTolerance: (po: PurchaseOrder) => void;
}

export const POActionMenu: React.FC<POActionMenuProps> = ({
  purchaseOrder,
  currentUserRole = 'user',
  onUploadReceipt,
  onUploadInvoice,
  onViewPO,
  onViewMatches,
  onClosePO,
  onOverrideTolerance,
}) => {
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
  const open = Boolean(anchorEl);

  const handleClick = (event: React.MouseEvent<HTMLElement>) => {
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  const handleAction = (action: () => void) => {
    action();
    handleClose();
  };

  const canUploadReceipt = purchaseOrder.status !== 'closed' && purchaseOrder.status !== 'received';
  const canUploadInvoice = purchaseOrder.status !== 'closed';
  const canClosePO = purchaseOrder.status !== 'closed';
  const canOverrideTolerance = 
    currentUserRole === 'admin' && 
    purchaseOrder.matchStatus === 'exception';

  return (
    <>
      <IconButton
        size="small"
        onClick={handleClick}
        aria-label="more actions"
        aria-controls={open ? 'po-action-menu' : undefined}
        aria-haspopup="true"
        aria-expanded={open ? 'true' : undefined}
      >
        <MoreVertIcon />
      </IconButton>
      <Menu
        id="po-action-menu"
        anchorEl={anchorEl}
        open={open}
        onClose={handleClose}
        MenuListProps={{
          'aria-labelledby': 'po-action-button',
        }}
        transformOrigin={{ horizontal: 'right', vertical: 'top' }}
        anchorOrigin={{ horizontal: 'right', vertical: 'bottom' }}
      >
        {/* Upload Receipt */}
        {canUploadReceipt && (
          <MenuItem onClick={() => handleAction(() => onUploadReceipt(purchaseOrder))}>
            <ListItemIcon>
              <UploadIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>Upload Receipt</ListItemText>
          </MenuItem>
        )}

        {/* Upload Invoice */}
        {canUploadInvoice && (
          <MenuItem onClick={() => handleAction(() => onUploadInvoice(purchaseOrder))}>
            <ListItemIcon>
              <ReceiptIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>Upload Invoice</ListItemText>
          </MenuItem>
        )}

        {(canUploadReceipt || canUploadInvoice) && <Divider />}

        {/* View PO */}
        <MenuItem onClick={() => handleAction(() => onViewPO(purchaseOrder))}>
          <ListItemIcon>
            <DescriptionIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>View PO</ListItemText>
        </MenuItem>

        {/* View Matches */}
        <MenuItem onClick={() => handleAction(() => onViewMatches(purchaseOrder))}>
          <ListItemIcon>
            <LinkIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>View Matches</ListItemText>
        </MenuItem>

        <Divider />

        {/* Close PO */}
        {canClosePO && (
          <MenuItem onClick={() => handleAction(() => onClosePO(purchaseOrder))}>
            <ListItemIcon>
              <CloseIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>Close PO</ListItemText>
          </MenuItem>
        )}

        {/* Override Tolerance (Admin Only) */}
        {canOverrideTolerance && (
          <MenuItem onClick={() => handleAction(() => onOverrideTolerance(purchaseOrder))}>
            <ListItemIcon>
              <CheckCircleIcon fontSize="small" color="warning" />
            </ListItemIcon>
            <ListItemText>Override Tolerance</ListItemText>
          </MenuItem>
        )}
      </Menu>
    </>
  );
};
