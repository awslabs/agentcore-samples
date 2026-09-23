import React from 'react';
import {
  Paper,
  Typography,
  Grid,
  Box,
  Chip,
  Button,
  Link as MuiLink,
} from '@mui/material';
import {
  CheckCircle as CheckCircleIcon,
  Warning as WarningIcon,
  Error as ErrorIcon,
  Link as LinkIcon,
} from '@mui/icons-material';
import type { Invoice } from '../../types/invoice.types';
import { formatCurrency } from '../../utils/formatters';

interface MatchedPOCardProps {
  invoice: Invoice;
  onLinkPO: () => void;
}

const getVarianceColor = (variance: number) => {
  if (variance < 5) return 'success';
  if (variance < 10) return 'warning';
  return 'error';
};

const getVarianceIcon = (variance: number) => {
  if (variance < 5) return <CheckCircleIcon />;
  if (variance < 10) return <WarningIcon />;
  return <ErrorIcon />;
};

export const MatchedPOCard: React.FC<MatchedPOCardProps> = ({
  invoice,
  onLinkPO,
}) => {
  if (!invoice.hasPoMatch || !invoice.purchaseOrder) {
    return (
      <Paper sx={{ p: 3 }}>
        <Typography variant="h6" gutterBottom fontWeight="bold">
          Purchase Order Match
        </Typography>
        <Box
          sx={{
            textAlign: 'center',
            py: 4,
            bgcolor: 'grey.50',
            borderRadius: 1,
          }}
        >
          <Typography variant="body1" color="text.secondary" gutterBottom>
            No Purchase Order Match
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            This invoice has not been linked to a purchase order yet.
          </Typography>
          <Button
            variant="contained"
            startIcon={<LinkIcon />}
            onClick={onLinkPO}
          >
            Link to Purchase Order
          </Button>
        </Box>
      </Paper>
    );
  }

  const po = invoice.purchaseOrder;
  const variance = invoice.variancePercentage || 0;
  const varianceColor = getVarianceColor(variance);

  return (
    <Paper sx={{ p: 3 }}>
      <Typography variant="h6" gutterBottom fontWeight="bold">
        Matched Purchase Order
      </Typography>

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, sm: 6 }}>
          <Typography variant="caption" color="text.secondary">
            PO Number
          </Typography>
          <Box display="flex" alignItems="center" gap={1}>
            <MuiLink
              href={`/purchase-orders/${po.id}`}
              underline="hover"
              fontWeight="medium"
            >
              {po.poNumber}
            </MuiLink>
            <Chip label="Matched" color="success" size="small" />
          </Box>
        </Grid>

        <Grid size={{ xs: 12, sm: 6 }}>
          <Typography variant="caption" color="text.secondary">
            PO Date
          </Typography>
          <Typography variant="body2">
            {new Date(po.assignedDate).toLocaleDateString()}
          </Typography>
        </Grid>

        <Grid size={{ xs: 12, sm: 6 }}>
          <Typography variant="caption" color="text.secondary">
            PO Amount
          </Typography>
          <Typography variant="body2" fontWeight="medium">
            {formatCurrency(po.totalAmount, invoice.currency)}
          </Typography>
        </Grid>

        <Grid size={{ xs: 12, sm: 6 }}>
          <Typography variant="caption" color="text.secondary">
            Invoice Amount
          </Typography>
          <Typography variant="body2" fontWeight="medium">
            {formatCurrency(invoice.totalAmount, invoice.currency)}
          </Typography>
        </Grid>

        <Grid size={{ xs: 12 }}>
          <Typography variant="caption" color="text.secondary">
            Variance
          </Typography>
          <Box display="flex" alignItems="center" gap={1} mt={0.5}>
            <Chip
              icon={getVarianceIcon(variance)}
              label={`${variance.toFixed(2)}%`}
              color={varianceColor}
              size="small"
            />
            {variance < 5 && (
              <Typography variant="caption" color="success.main">
                Within acceptable range
              </Typography>
            )}
            {variance >= 5 && variance < 10 && (
              <Typography variant="caption" color="warning.main">
                Moderate variance - review recommended
              </Typography>
            )}
            {variance >= 10 && (
              <Typography variant="caption" color="error.main">
                High variance - requires approval
              </Typography>
            )}
          </Box>
        </Grid>

        <Grid size={{ xs: 12, sm: 6 }}>
          <Typography variant="caption" color="text.secondary">
            Vendor Match
          </Typography>
          <Box display="flex" alignItems="center" gap={1}>
            {po.vendor.name === invoice.vendor?.name ? (
              <>
                <CheckCircleIcon color="success" fontSize="small" />
                <Typography variant="body2" color="success.main">
                  Vendor matches
                </Typography>
              </>
            ) : (
              <>
                <WarningIcon color="warning" fontSize="small" />
                <Typography variant="body2" color="warning.main">
                  Vendor mismatch
                </Typography>
              </>
            )}
          </Box>
        </Grid>

        <Grid size={{ xs: 12, sm: 6 }}>
          <Typography variant="caption" color="text.secondary">
            PO Status
          </Typography>
          <Typography variant="body2">{po.status}</Typography>
        </Grid>
      </Grid>
    </Paper>
  );
};
