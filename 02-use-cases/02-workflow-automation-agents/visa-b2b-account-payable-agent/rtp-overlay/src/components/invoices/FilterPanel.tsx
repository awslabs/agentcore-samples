import React, { useState } from 'react';
import {
  Box,
  Grid,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  TextField,
  Chip,
  OutlinedInput,
} from '@mui/material';
import type { SelectChangeEvent } from '@mui/material/Select';
import FilterListIcon from '@mui/icons-material/FilterList';
import ClearIcon from '@mui/icons-material/Clear';
import { QBCard } from '../common/QBCard';
import { QBButton } from '../common/QBButton';
import type { InvoiceFilters, PaymentStatus } from '../../types/invoice.types';

interface FilterPanelProps {
  filters: InvoiceFilters;
  onFilterChange: (filters: InvoiceFilters) => void;
  onClearFilters: () => void;
}

const paymentStatuses: PaymentStatus[] = [
  'pending',
  'processing',
  'generated',
  'sent',
  'paid',
  'failed',
];

const statusLabels: Record<PaymentStatus, string> = {
  pending: 'Pending',
  processing: 'Processing',
  generated: 'Generated',
  sent: 'Sent',
  paid: 'Paid',
  failed: 'Failed',
};

export const FilterPanel: React.FC<FilterPanelProps> = ({
  filters,
  onFilterChange,
  onClearFilters,
}) => {
  const [localFilters, setLocalFilters] = useState<InvoiceFilters>(filters);

  const handleStatusChange = (event: SelectChangeEvent<PaymentStatus[]>) => {
    const value = event.target.value;
    const newFilters = {
      ...localFilters,
      status: typeof value === 'string' ? (value.split(',') as PaymentStatus[]) : value,
    };
    setLocalFilters(newFilters);
    onFilterChange(newFilters);
  };

  const handlePoMatchChange = (event: SelectChangeEvent<string>) => {
    const newFilters = {
      ...localFilters,
      poMatchStatus: event.target.value as 'all' | 'matched' | 'unmatched' | 'variance',
    };
    setLocalFilters(newFilters);
    onFilterChange(newFilters);
  };

  const handleStartDateChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const newFilters = {
      ...localFilters,
      startDate: event.target.value || null,
    };
    setLocalFilters(newFilters);
    onFilterChange(newFilters);
  };

  const handleEndDateChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const newFilters = {
      ...localFilters,
      endDate: event.target.value || null,
    };
    setLocalFilters(newFilters);
    onFilterChange(newFilters);
  };

  const handleClear = () => {
    setLocalFilters({
      status: [],
      vendorId: null,
      startDate: null,
      endDate: null,
      poMatchStatus: 'all',
    });
    onClearFilters();
  };

  const hasActiveFilters =
    localFilters.status.length > 0 ||
    localFilters.vendorId ||
    localFilters.startDate ||
    localFilters.endDate ||
    localFilters.poMatchStatus !== 'all';

  return (
    <QBCard sx={{ mb: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
        <FilterListIcon sx={{ mr: 1, color: 'text.secondary' }} />
        <Box sx={{ flexGrow: 1, fontWeight: 600 }}>Filters</Box>
        {hasActiveFilters && (
          <QBButton
            startIcon={<ClearIcon />}
            onClick={handleClear}
            size="small"
            variant="secondary"
          >
            Clear Filters
          </QBButton>
        )}
      </Box>

      <Grid container spacing={2}>
        {/* Payment Status Filter */}
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <FormControl fullWidth size="small">
            <InputLabel id="status-filter-label">Payment Status</InputLabel>
            <Select
              labelId="status-filter-label"
              id="status-filter"
              multiple
              value={localFilters.status}
              onChange={handleStatusChange}
              input={<OutlinedInput label="Payment Status" />}
              renderValue={(selected) => (
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                  {selected.map((value) => (
                    <Chip key={value} label={statusLabels[value]} size="small" />
                  ))}
                </Box>
              )}
            >
              {paymentStatuses.map((status) => (
                <MenuItem key={status} value={status}>
                  {statusLabels[status]}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Grid>

        {/* PO Match Status Filter */}
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <FormControl fullWidth size="small">
            <InputLabel id="po-match-filter-label">PO Match Status</InputLabel>
            <Select
              labelId="po-match-filter-label"
              id="po-match-filter"
              value={localFilters.poMatchStatus}
              onChange={handlePoMatchChange}
              label="PO Match Status"
            >
              <MenuItem value="all">All</MenuItem>
              <MenuItem value="matched">Matched</MenuItem>
              <MenuItem value="unmatched">Unmatched</MenuItem>
              <MenuItem value="variance">Variance Warning</MenuItem>
            </Select>
          </FormControl>
        </Grid>

        {/* Start Date Filter */}
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <TextField
            fullWidth
            size="small"
            label="Start Date"
            type="date"
            value={localFilters.startDate || ''}
            onChange={handleStartDateChange}
            InputLabelProps={{
              shrink: true,
            }}
          />
        </Grid>

        {/* End Date Filter */}
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <TextField
            fullWidth
            size="small"
            label="End Date"
            type="date"
            value={localFilters.endDate || ''}
            onChange={handleEndDateChange}
            InputLabelProps={{
              shrink: true,
            }}
          />
        </Grid>
      </Grid>
    </QBCard>
  );
};
