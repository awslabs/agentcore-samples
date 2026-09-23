import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  Typography,
  Paper,
  Alert,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from '@mui/material';
import { ArrowBack, CheckCircle, Cancel } from '@mui/icons-material';
import { oilGasTheme } from '../../styles/oilGasTheme';
import { mockDataService } from '../../services/mockDataService';
import QCStatusBadge from '../../components/common/QCStatusBadge';

export const QCRecordDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const qcRecord = mockDataService.getQCRecords().find((qc) => qc.id === id);
  const goodsReceipt = qcRecord?.grId
    ? mockDataService.getGoodsReceipts().find((gr) => gr.id === qcRecord.grId)
    : null;

  if (!qcRecord) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error">QC Record not found</Alert>
        <Button onClick={() => navigate('/quality-control')} sx={{ mt: 2 }}>
          Back to List
        </Button>
      </Box>
    );
  }

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  };

  return (
    <Box sx={{ p: 3 }}>
      {/* Header */}
      <Box sx={{ mb: 3, display: 'flex', alignItems: 'center', gap: 2 }}>
        <Button
          startIcon={<ArrowBack />}
          onClick={() => navigate('/quality-control')}
          sx={{ color: oilGasTheme.colors.textMuted }}
        >
          Back
        </Button>
        <Box sx={{ flex: 1 }}>
          <Typography variant="h4" sx={{ color: oilGasTheme.colors.primary }}>
            {qcRecord.qcNumber}
          </Typography>
          <Typography variant="body2" color="textSecondary">
            Quality Control Record
          </Typography>
        </Box>
        <QCStatusBadge status={qcRecord.overallStatus} />
      </Box>

      {/* Overall Status Alert */}
      <Alert
        severity={qcRecord.overallStatus === 'PASS' ? 'success' : 'error'}
        icon={qcRecord.overallStatus === 'PASS' ? <CheckCircle /> : <Cancel />}
        sx={{ mb: 3 }}
      >
        <Typography variant="h6">
          Overall Status: <strong>{qcRecord.overallStatus}</strong>
        </Typography>
        {qcRecord.overallStatus === 'FAIL' && (
          <Typography variant="body2">
            One or more measurements failed specification. This material may not be approved for use.
          </Typography>
        )}
        {qcRecord.overallStatus === 'PASS' && (
          <Typography variant="body2">All measurements meet specifications. Material approved.</Typography>
        )}
      </Alert>

      {/* QC Header Information */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Test Information
        </Typography>
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 3 }}>
          <Box>
            <Typography variant="caption" color="textSecondary">
              QC Number
            </Typography>
            <Typography variant="body1" sx={{ fontWeight: 'bold' }}>
              {qcRecord.qcNumber}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="textSecondary">
              Test Date
            </Typography>
            <Typography variant="body1">{formatDate(qcRecord.testDate)}</Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="textSecondary">
              Inspector
            </Typography>
            <Typography variant="body1">{qcRecord.inspectorName}</Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="textSecondary">
              Lab Reference
            </Typography>
            <Typography variant="body1">{qcRecord.labReference || 'N/A'}</Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="textSecondary">
              Created
            </Typography>
            <Typography variant="body1">{formatDate(qcRecord.createdAt)}</Typography>
          </Box>
        </Box>
      </Paper>

      {/* Material Details */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Material Details
        </Typography>
        <Box sx={{ mb: 2 }}>
          <Typography variant="caption" color="textSecondary">
            Material Description
          </Typography>
          <Typography variant="body1" sx={{ fontWeight: 'bold' }}>
            {qcRecord.materialDescription}
          </Typography>
        </Box>
      </Paper>

      {/* Linked Documents */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Linked Documents
        </Typography>
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 3 }}>
          <Box>
            <Typography variant="caption" color="textSecondary">
              Purchase Order
            </Typography>
            <Typography variant="body1">
              <Button
                variant="text"
                onClick={() => navigate(`/purchase-orders/${qcRecord.poId}`)}
                sx={{ p: 0, textTransform: 'none', color: oilGasTheme.colors.primary }}
              >
                {qcRecord.poNumber}
              </Button>
            </Typography>
          </Box>
          {goodsReceipt && (
            <Box>
              <Typography variant="caption" color="textSecondary">
                Goods Receipt
              </Typography>
              <Typography variant="body1">
                <Button
                  variant="text"
                  onClick={() => navigate(`/goods-receipts/${goodsReceipt.id}`)}
                  sx={{ p: 0, textTransform: 'none', color: oilGasTheme.colors.primary }}
                >
                  {qcRecord.grNumber}
                </Button>
              </Typography>
            </Box>
          )}
        </Box>
      </Paper>

      {/* Measurements Table */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Quality Measurements
        </Typography>
        <TableContainer>
          <Table>
            <TableHead>
              <TableRow sx={{ backgroundColor: oilGasTheme.colors.background }}>
                <TableCell>
                  <strong>Parameter</strong>
                </TableCell>
                <TableCell>
                  <strong>Measured Value</strong>
                </TableCell>
                <TableCell>
                  <strong>Specification</strong>
                </TableCell>
                <TableCell align="center">
                  <strong>Status</strong>
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {qcRecord.measurements.map((measurement, index) => (
                <TableRow
                  key={index}
                  sx={{
                    backgroundColor:
                      measurement.status === 'FAIL'
                        ? oilGasTheme.colors.danger + '10'
                        : measurement.status === 'PASS'
                        ? oilGasTheme.colors.success + '10'
                        : 'transparent',
                  }}
                >
                  <TableCell>{measurement.parameter}</TableCell>
                  <TableCell>
                    <Typography sx={{ fontWeight: 'bold' }}>
                      {measurement.value} {measurement.unit}
                    </Typography>
                  </TableCell>
                  <TableCell>{measurement.specification}</TableCell>
                  <TableCell align="center">
                    {measurement.status === 'PASS' && (
                      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1 }}>
                        <CheckCircle sx={{ color: oilGasTheme.colors.success }} />
                        <Typography sx={{ color: oilGasTheme.colors.success, fontWeight: 'bold' }}>
                          PASS
                        </Typography>
                      </Box>
                    )}
                    {measurement.status === 'FAIL' && (
                      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1 }}>
                        <Cancel sx={{ color: oilGasTheme.colors.danger }} />
                        <Typography sx={{ color: oilGasTheme.colors.danger, fontWeight: 'bold' }}>
                          FAIL
                        </Typography>
                      </Box>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      {/* Notes */}
      {qcRecord.notes && (
        <Paper sx={{ p: 3 }}>
          <Typography variant="h6" sx={{ mb: 2 }}>
            Notes
          </Typography>
          <Typography variant="body1">{qcRecord.notes}</Typography>
        </Paper>
      )}
    </Box>
  );
};
