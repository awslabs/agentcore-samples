import React from 'react';
import {
  Paper,
  Typography,
  Grid,
  Box,
  Button,
} from '@mui/material';
import {
  LocalShipping as ShippingIcon,
  AttachFile as AttachFileIcon,
} from '@mui/icons-material';
import type { Invoice } from '../../types/invoice.types';

interface GoodsReceiptCardProps {
  invoice: Invoice;
}

export const GoodsReceiptCard: React.FC<GoodsReceiptCardProps> = ({
  invoice,
}) => {
  if (!invoice.goodsReceipt) {
    return (
      <Paper sx={{ p: 3 }}>
        <Typography variant="h6" gutterBottom fontWeight="bold">
          Goods Receipt
        </Typography>
        <Box
          sx={{
            textAlign: 'center',
            py: 4,
            bgcolor: 'grey.50',
            borderRadius: 1,
          }}
        >
          <ShippingIcon sx={{ fontSize: 48, color: 'text.secondary', mb: 1 }} />
          <Typography variant="body1" color="text.secondary">
            No Goods Receipt Linked
          </Typography>
          <Typography variant="body2" color="text.secondary">
            This invoice has not been linked to a goods receipt yet.
          </Typography>
        </Box>
      </Paper>
    );
  }

  const gr = invoice.goodsReceipt;

  return (
    <Paper sx={{ p: 3 }}>
      <Typography variant="h6" gutterBottom fontWeight="bold">
        Goods Receipt
      </Typography>

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, sm: 6 }}>
          <Typography variant="caption" color="text.secondary">
            Delivery Reference
          </Typography>
          <Typography variant="body2" fontWeight="medium">
            {gr.deliveryReference}
          </Typography>
        </Grid>

        <Grid size={{ xs: 12, sm: 6 }}>
          <Typography variant="caption" color="text.secondary">
            Delivery Date
          </Typography>
          <Typography variant="body2">
            {new Date(gr.deliveryDate).toLocaleDateString()}
          </Typography>
        </Grid>

        <Grid size={{ xs: 12, sm: 6 }}>
          <Typography variant="caption" color="text.secondary">
            Quantity Received
          </Typography>
          <Typography variant="body2">{gr.quantityReceived}</Typography>
        </Grid>

        <Grid size={{ xs: 12, sm: 6 }}>
          <Typography variant="caption" color="text.secondary">
            Received By
          </Typography>
          <Typography variant="body2">{gr.receivedBy.name}</Typography>
        </Grid>

        {gr.conditionNotes && (
          <Grid size={{ xs: 12 }}>
            <Typography variant="caption" color="text.secondary">
              Condition Notes
            </Typography>
            <Typography variant="body2">{gr.conditionNotes}</Typography>
          </Grid>
        )}

        {gr.documents && gr.documents.length > 0 && (
          <Grid size={{ xs: 12 }}>
            <Box display="flex" alignItems="center" gap={1}>
              <AttachFileIcon fontSize="small" color="action" />
              <Typography variant="body2" color="text.secondary">
                {gr.documents.length} document(s) attached
              </Typography>
              <Button size="small">View Documents</Button>
            </Box>
          </Grid>
        )}
      </Grid>
    </Paper>
  );
};
