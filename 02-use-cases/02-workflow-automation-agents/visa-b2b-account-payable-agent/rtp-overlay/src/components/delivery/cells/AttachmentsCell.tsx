import React from 'react';
import { Box, IconButton, Tooltip, Typography } from '@mui/material';
import AttachFileIcon from '@mui/icons-material/AttachFile';
import type { POAttachments } from '../../../types/data.types';

interface AttachmentsCellProps {
  attachments: POAttachments;
  onViewAttachments?: () => void;
}

export const AttachmentsCell: React.FC<AttachmentsCellProps> = ({
  attachments,
  onViewAttachments,
}) => {
  // Provide default values if attachments is undefined
  const safeAttachments = attachments || {
    poDocuments: 0,
    receipts: 0,
    invoices: 0,
    total: 0,
  };

  const tooltipContent = (
    <Box>
      <Typography variant="caption" display="block">
        PO Documents: {safeAttachments.poDocuments}
      </Typography>
      <Typography variant="caption" display="block">
        Receipts: {safeAttachments.receipts}
      </Typography>
      <Typography variant="caption" display="block">
        Invoices: {safeAttachments.invoices}
      </Typography>
    </Box>
  );

  return (
    <Tooltip title={tooltipContent} arrow>
      <IconButton
        size="small"
        onClick={onViewAttachments}
        sx={{
          '&:hover': {
            backgroundColor: 'action.hover',
          },
        }}
      >
        <AttachFileIcon fontSize="small" />
        <Typography variant="caption" sx={{ ml: 0.5, fontWeight: 600 }}>
          {safeAttachments.total}
        </Typography>
      </IconButton>
    </Tooltip>
  );
};
