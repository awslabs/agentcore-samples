import React, { useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Grid,
  Box,
  IconButton,
  Typography,
  Autocomplete,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import type { Vendor, PurchaseOrderItem } from '../../types/data.types';

interface CreatePODialogProps {
  open: boolean;
  vendors: Vendor[];
  onClose: () => void;
  onSubmit: (poData: {
    poNumber: string;
    vendor: Vendor;
    sourceLocation: string;
    dueDate: string;
    warehouse?: string;
    items: Omit<PurchaseOrderItem, 'id' | 'receivedQuantity'>[];
  }) => Promise<void>;
}

interface LineItem {
  description: string;
  quantity: string;
  unitPrice: string;
}

export const CreatePODialog: React.FC<CreatePODialogProps> = ({
  open,
  vendors,
  onClose,
  onSubmit,
}) => {
  const [poNumber, setPONumber] = useState('');
  const [selectedVendor, setSelectedVendor] = useState<Vendor | null>(null);
  const [sourceLocation, setSourceLocation] = useState('');
  const [dueDate, setDueDate] = useState('');
  const [warehouse, setWarehouse] = useState('');
  const [lineItems, setLineItems] = useState<LineItem[]>([
    { description: '', quantity: '', unitPrice: '' },
  ]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleAddLineItem = () => {
    setLineItems([...lineItems, { description: '', quantity: '', unitPrice: '' }]);
  };

  const handleRemoveLineItem = (index: number) => {
    if (lineItems.length > 1) {
      setLineItems(lineItems.filter((_, i) => i !== index));
    }
  };

  const handleLineItemChange = (
    index: number,
    field: keyof LineItem,
    value: string
  ) => {
    const newLineItems = [...lineItems];
    newLineItems[index][field] = value;
    setLineItems(newLineItems);
  };

  const calculateTotal = (): number => {
    return lineItems.reduce((sum, item) => {
      const qty = parseFloat(item.quantity) || 0;
      const price = parseFloat(item.unitPrice) || 0;
      return sum + qty * price;
    }, 0);
  };

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (!poNumber.trim()) {
      newErrors.poNumber = 'PO Number is required';
    }

    if (!selectedVendor) {
      newErrors.vendor = 'Vendor is required';
    }

    if (!sourceLocation.trim()) {
      newErrors.sourceLocation = 'Source Location is required';
    }

    if (!dueDate) {
      newErrors.dueDate = 'Due Date is required';
    }

    lineItems.forEach((item, index) => {
      if (!item.description.trim()) {
        newErrors[`item${index}Description`] = 'Description is required';
      }
      if (!item.quantity || parseFloat(item.quantity) <= 0) {
        newErrors[`item${index}Quantity`] = 'Valid quantity required';
      }
      if (!item.unitPrice || parseFloat(item.unitPrice) <= 0) {
        newErrors[`item${index}UnitPrice`] = 'Valid unit price required';
      }
    });

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async () => {
    if (!validate() || !selectedVendor) return;

    setIsSubmitting(true);
    try {
      const items = lineItems.map((item) => {
        const quantity = parseFloat(item.quantity);
        const unitPrice = parseFloat(item.unitPrice);
        return {
          description: item.description,
          quantity,
          unitPrice,
          totalPrice: quantity * unitPrice,
        };
      });

      await onSubmit({
        poNumber,
        vendor: selectedVendor,
        sourceLocation,
        dueDate,
        warehouse: warehouse || undefined,
        items,
      });

      handleClose();
    } catch (error) {
      console.error('Error creating PO:', error);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleClose = () => {
    setPONumber('');
    setSelectedVendor(null);
    setSourceLocation('');
    setDueDate('');
    setWarehouse('');
    setLineItems([{ description: '', quantity: '', unitPrice: '' }]);
    setErrors({});
    onClose();
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle>Create Purchase Order</DialogTitle>
      <DialogContent>
        <Box sx={{ mt: 2 }}>
          <Grid container spacing={2}>
            {/* PO Number */}
            <Grid size={{ xs: 12, sm: 6 }}>
              <TextField
                fullWidth
                label="PO Number"
                value={poNumber}
                onChange={(e) => setPONumber(e.target.value)}
                error={!!errors.poNumber}
                helperText={errors.poNumber}
                required
              />
            </Grid>

            {/* Vendor */}
            <Grid size={{ xs: 12, sm: 6 }}>
              <Autocomplete
                options={vendors}
                getOptionLabel={(option) => option.name}
                value={selectedVendor}
                onChange={(_, value) => setSelectedVendor(value)}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="Vendor"
                    error={!!errors.vendor}
                    helperText={errors.vendor}
                    required
                  />
                )}
              />
            </Grid>

            {/* Source Location */}
            <Grid size={{ xs: 12, sm: 6 }}>
              <TextField
                fullWidth
                label="Source Location"
                value={sourceLocation}
                onChange={(e) => setSourceLocation(e.target.value)}
                error={!!errors.sourceLocation}
                helperText={errors.sourceLocation}
                required
              />
            </Grid>

            {/* Due Date */}
            <Grid size={{ xs: 12, sm: 6 }}>
              <TextField
                fullWidth
                label="Expected Delivery Date"
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
                error={!!errors.dueDate}
                helperText={errors.dueDate}
                InputLabelProps={{ shrink: true }}
                required
              />
            </Grid>

            {/* Warehouse (Optional) */}
            <Grid size={{ xs: 12 }}>
              <TextField
                fullWidth
                label="Warehouse (Optional)"
                value={warehouse}
                onChange={(e) => setWarehouse(e.target.value)}
              />
            </Grid>
          </Grid>

          {/* Line Items Section */}
          <Box sx={{ mt: 3 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
              <Typography variant="h6">Line Items</Typography>
              <Button
                startIcon={<AddIcon />}
                onClick={handleAddLineItem}
                size="small"
                variant="outlined"
              >
                Add Item
              </Button>
            </Box>

            {lineItems.map((item, index) => (
              <Box key={index} sx={{ mb: 2, p: 2, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
                <Grid container spacing={2}>
                  <Grid size={{ xs: 12 }}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Typography variant="subtitle2">Item {index + 1}</Typography>
                      {lineItems.length > 1 && (
                        <IconButton
                          size="small"
                          onClick={() => handleRemoveLineItem(index)}
                          color="error"
                        >
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      )}
                    </Box>
                  </Grid>

                  <Grid size={{ xs: 12 }}>
                    <TextField
                      fullWidth
                      label="Description"
                      value={item.description}
                      onChange={(e) => handleLineItemChange(index, 'description', e.target.value)}
                      error={!!errors[`item${index}Description`]}
                      helperText={errors[`item${index}Description`]}
                      required
                    />
                  </Grid>

                  <Grid size={{ xs: 12, sm: 4 }}>
                    <TextField
                      fullWidth
                      label="Quantity"
                      type="number"
                      value={item.quantity}
                      onChange={(e) => handleLineItemChange(index, 'quantity', e.target.value)}
                      error={!!errors[`item${index}Quantity`]}
                      helperText={errors[`item${index}Quantity`]}
                      inputProps={{ min: 0, step: 1 }}
                      required
                    />
                  </Grid>

                  <Grid size={{ xs: 12, sm: 4 }}>
                    <TextField
                      fullWidth
                      label="Unit Price"
                      type="number"
                      value={item.unitPrice}
                      onChange={(e) => handleLineItemChange(index, 'unitPrice', e.target.value)}
                      error={!!errors[`item${index}UnitPrice`]}
                      helperText={errors[`item${index}UnitPrice`]}
                      inputProps={{ min: 0, step: 0.01 }}
                      required
                    />
                  </Grid>

                  <Grid size={{ xs: 12, sm: 4 }}>
                    <TextField
                      fullWidth
                      label="Total"
                      value={
                        (parseFloat(item.quantity) || 0) * (parseFloat(item.unitPrice) || 0)
                      }
                      InputProps={{ readOnly: true }}
                      disabled
                    />
                  </Grid>
                </Grid>
              </Box>
            ))}

            {/* Total Amount */}
            <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 2 }}>
              <Typography variant="h6">
                Total Amount: ${calculateTotal().toFixed(2)}
              </Typography>
            </Box>
          </Box>
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button
          onClick={handleSubmit}
          variant="contained"
          disabled={isSubmitting}
        >
          {isSubmitting ? 'Creating...' : 'Create PO'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};
