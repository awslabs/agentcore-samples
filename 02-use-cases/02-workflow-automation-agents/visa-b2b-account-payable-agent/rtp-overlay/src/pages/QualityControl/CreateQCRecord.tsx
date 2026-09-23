import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  Typography,
  Paper,
  TextField,
  MenuItem,
  Alert,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from '@mui/material';
import { CheckCircle, Cancel } from '@mui/icons-material';
import { oilGasTheme } from '../../styles/oilGasTheme';
import { mockDataService, type PurchaseOrder } from '../../services/mockDataService';

interface Measurement {
  parameter: string;
  value: string;
  unit: string;
  min?: number;
  max?: number;
  status: 'PASS' | 'FAIL' | 'PENDING';
}

export const CreateQCRecord: React.FC = () => {
  const navigate = useNavigate();
  const [selectedPoId, setSelectedPoId] = useState('');
  const [selectedPo, setSelectedPo] = useState<PurchaseOrder | null>(null);
  const [inspectorName, setInspectorName] = useState('');
  const [testDate, setTestDate] = useState(new Date().toISOString().split('T')[0]);
  const [labReference, setLabReference] = useState('');
  const [notes, setNotes] = useState('');
  const [measurements, setMeasurements] = useState<Measurement[]>([]);

  const purchaseOrders = mockDataService.getPurchaseOrders();

  const handlePoSelect = (poId: string) => {
    setSelectedPoId(poId);
    const po = purchaseOrders.find((p) => p.id === poId);
    if (po) {
      setSelectedPo(po);
      // Initialize measurements from PO quality specs
      const initialMeasurements: Measurement[] = po.qualitySpecs.map((spec) => ({
        parameter: spec.parameter,
        value: '',
        unit: spec.unit,
        min: spec.min,
        max: spec.max,
        status: 'PENDING',
      }));
      setMeasurements(initialMeasurements);
    }
  };

  const handleMeasurementChange = (index: number, value: string) => {
    const newMeasurements = [...measurements];
    newMeasurements[index].value = value;

    // Auto-validate against spec
    const numValue = parseFloat(value);
    if (!isNaN(numValue)) {
      const measurement = newMeasurements[index];
      let pass = true;

      if (measurement.min !== undefined && numValue < measurement.min) {
        pass = false;
      }
      if (measurement.max !== undefined && numValue > measurement.max) {
        pass = false;
      }

      newMeasurements[index].status = pass ? 'PASS' : 'FAIL';
    } else {
      newMeasurements[index].status = 'PENDING';
    }

    setMeasurements(newMeasurements);
  };

  const getOverallStatus = (): 'PASS' | 'FAIL' | 'PENDING' => {
    if (measurements.length === 0) return 'PENDING';
    if (measurements.some((m) => m.status === 'PENDING' || m.value === '')) return 'PENDING';
    if (measurements.some((m) => m.status === 'FAIL')) return 'FAIL';
    return 'PASS';
  };

  const handleSubmit = () => {
    if (!selectedPo || !inspectorName || measurements.some((m) => m.value === '')) {
      alert('Please fill in all required fields and measurements');
      return;
    }

    // In real implementation, this would save to backend
    const overallStatus = getOverallStatus();
    alert(`QC Record created successfully!\nOverall Status: ${overallStatus}`);
    navigate('/quality-control');
  };

  const overallStatus = getOverallStatus();

  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h4" sx={{ mb: 3, color: oilGasTheme.colors.primary }}>
        Create Quality Control Record
      </Typography>

      {/* PO Selection */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Select Purchase Order
        </Typography>
        <TextField
          select
          label="Purchase Order"
          value={selectedPoId}
          onChange={(e) => handlePoSelect(e.target.value)}
          fullWidth
          required
        >
          <MenuItem value="">
            <em>Select a PO</em>
          </MenuItem>
          {purchaseOrders.map((po) => (
            <MenuItem key={po.id} value={po.id}>
              {po.poNumber} - {po.materialDescription} ({po.vendorName})
            </MenuItem>
          ))}
        </TextField>
      </Paper>

      {/* PO Details */}
      {selectedPo && (
        <>
          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h6" sx={{ mb: 2 }}>
              Purchase Order Details
            </Typography>
            <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2 }}>
              <Box>
                <Typography variant="caption" color="textSecondary">
                  PO Number
                </Typography>
                <Typography variant="body1">{selectedPo.poNumber}</Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="textSecondary">
                  Vendor
                </Typography>
                <Typography variant="body1">{selectedPo.vendorName}</Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="textSecondary">
                  Material
                </Typography>
                <Typography variant="body1">{selectedPo.materialDescription}</Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="textSecondary">
                  Quantity
                </Typography>
                <Typography variant="body1">
                  {selectedPo.quantity.toLocaleString()} {selectedPo.unit}
                </Typography>
              </Box>
            </Box>
          </Paper>

          {/* QC Information */}
          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h6" sx={{ mb: 2 }}>
              QC Test Information
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <TextField
                label="Inspector Name"
                value={inspectorName}
                onChange={(e) => setInspectorName(e.target.value)}
                fullWidth
                required
              />
              <TextField
                label="Test Date"
                type="date"
                value={testDate}
                onChange={(e) => setTestDate(e.target.value)}
                fullWidth
                required
                InputLabelProps={{ shrink: true }}
              />
              <TextField
                label="Lab Reference Number"
                value={labReference}
                onChange={(e) => setLabReference(e.target.value)}
                fullWidth
                placeholder="Optional"
              />
            </Box>
          </Paper>

          {/* Quality Measurements */}
          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h6" sx={{ mb: 2 }}>
              Quality Measurements
            </Typography>
            <Alert severity="info" sx={{ mb: 2 }}>
              Enter measured values. The system will automatically validate against specifications and show
              PASS/FAIL status.
            </Alert>

            <TableContainer>
              <Table>
                <TableHead>
                  <TableRow sx={{ backgroundColor: oilGasTheme.colors.background }}>
                    <TableCell>
                      <strong>Parameter</strong>
                    </TableCell>
                    <TableCell>
                      <strong>Specification</strong>
                    </TableCell>
                    <TableCell>
                      <strong>Measured Value</strong>
                    </TableCell>
                    <TableCell align="center">
                      <strong>Status</strong>
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {measurements.map((measurement, index) => {
                    const specText =
                      measurement.min !== undefined && measurement.max !== undefined
                        ? `${measurement.min} - ${measurement.max} ${measurement.unit}`
                        : measurement.min !== undefined
                        ? `≥ ${measurement.min} ${measurement.unit}`
                        : measurement.max !== undefined
                        ? `≤ ${measurement.max} ${measurement.unit}`
                        : measurement.unit;

                    return (
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
                        <TableCell>{specText}</TableCell>
                        <TableCell>
                          <TextField
                            type="number"
                            value={measurement.value}
                            onChange={(e) => handleMeasurementChange(index, e.target.value)}
                            placeholder={`Enter ${measurement.unit}`}
                            size="small"
                            fullWidth
                            inputProps={{ step: 'any' }}
                          />
                        </TableCell>
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
                          {measurement.status === 'PENDING' && (
                            <Typography sx={{ color: oilGasTheme.colors.textMuted }}>-</Typography>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          </Paper>

          {/* Overall Status */}
          {overallStatus !== 'PENDING' && (
            <Alert
              severity={overallStatus === 'PASS' ? 'success' : 'error'}
              sx={{ mb: 3 }}
              icon={overallStatus === 'PASS' ? <CheckCircle /> : <Cancel />}
            >
              <Typography variant="h6">
                Overall Status: <strong>{overallStatus}</strong>
              </Typography>
              {overallStatus === 'FAIL' && (
                <Typography variant="body2">
                  One or more measurements failed specification. This material may not be approved for use.
                </Typography>
              )}
              {overallStatus === 'PASS' && (
                <Typography variant="body2">All measurements meet specifications. Material approved.</Typography>
              )}
            </Alert>
          )}

          {/* Notes */}
          <Paper sx={{ p: 3, mb: 3 }}>
            <TextField
              label="Notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              fullWidth
              multiline
              rows={3}
              placeholder="Optional notes or observations"
            />
          </Paper>

          {/* Actions */}
          <Box sx={{ display: 'flex', gap: 2, justifyContent: 'flex-end' }}>
            <Button variant="outlined" onClick={() => navigate('/quality-control')} sx={{ color: oilGasTheme.colors.textMuted }}>
              Cancel
            </Button>
            <Button
              variant="contained"
              onClick={handleSubmit}
              disabled={!inspectorName || measurements.some((m) => m.value === '')}
              sx={{
                backgroundColor: oilGasTheme.colors.primary,
                '&:hover': { backgroundColor: '#003366' },
              }}
            >
              Create QC Record
            </Button>
          </Box>
        </>
      )}
    </Box>
  );
};
