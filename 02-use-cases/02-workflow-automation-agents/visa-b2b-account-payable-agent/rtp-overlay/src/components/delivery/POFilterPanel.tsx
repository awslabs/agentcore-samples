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
  FormControlLabel,
  Switch,
  Autocomplete,
} from '@mui/material';
import type { SelectChangeEvent } from '@mui/material/Select';
import FilterListIcon from '@mui/icons-material/FilterList';
import ClearIcon from '@mui/icons-material/Clear';
import { QBCard } from '../common/QBCard';
import { QBButton } from '../common/QBButton';
import type { POFilters, POStatus, Vendor } from '../../types/data.types';

interface POFilterPanelProps {
  filters: POFilters;
  vendors: Vendor[];
  warehouses: string[];
  onFilterChange: (filters: POFilters) => void;
  onClearFilters: () => void;
}

const poStatuses: POStatus[] = [
  'active',
  'partially-received',
  'received',
  'invoiced',
  'closed',
  'exception',
];

const statusLabels: Record<POStatus, string> = {
  active: 'Active',
  'partially-received': 'Partially Received',
  received: 'Received',
  invoiced: 'Invoiced',
  closed: 'Closed',
  exception: 'Exception',
};

export const POFilterPanel: React.FC<POFilterPanelProps> = ({
  filters,
  vendors,
  warehouses,
  onFilterChange,
  onClearFilters,
}) => {
  const [localFilters, setLocalFilters] = useState<POFilters>(filters);

  const handleStatusChange = (event: SelectChangeEvent<POStatus[]>) => {
    const value = event.target.value;
    const newFilters = {
      ...localFilters,
      status: typeof value === 'string' ? (value.split(',') as POStatus[]) : value,
    };
    setLocalFilters(newFilters);
    onFilterChange(newFilters);
  };

  const handleVendorChange = (_event: any, value: Vendor | null) => {
    const newFilters = {
      ...localFilters,
      vendorId: value?.id || null,
    };
    setLocalFilters(newFilters);
    onFilterChange(newFilters);
  };

  const handleWarehouseChange = (event: SelectChangeEvent<string>) => {
    const newFilters = {
      ...localFilters,
      warehouseId: event.target.value || null,
    };
    setLocalFilters(newFilters);
    onFilterChange(newFilters);
  };

  const handleStartDateChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const newFilters = {
      ...localFilters,
      dateRange: {
        ...localFilters.dateRange,
        start: event.target.value || null,
      },
    };
    setLocalFilters(newFilters);
    onFilterChange(newFilters);
  };

  const handleEndDateChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const newFilters = {
      ...localFilters,
      dateRange: {
        ...localFilters.dateRange,
        end: event.target.value || null,
      },
    };
    setLocalFilters(newFilters);
    onFilterChange(newFilters);
  };

  const handleOverdueToggle = (event: React.ChangeEvent<HTMLInputElement>) => {
    const newFilters = {
      ...localFilters,
      overdueOnly: event.target.checked,
    };
    setLocalFilters(newFilters);
    onFilterChange(newFilters);
  };

  const handleExceptionsToggle = (event: React.ChangeEvent<HTMLInputElement>) => {
    const newFilters = {
      ...localFilters,
      exceptionsOnly: event.target.checked,
    };
    setLocalFilters(newFilters);
    onFilterChange(newFilters);
  };

  const handleClear = () => {
    const clearedFilters: POFilters = {
      status: [],
      vendorId: null,
      warehouseId: null,
      dateRange: {
        start: null,
        end: null,
      },
      overdueOnly: false,
      exceptionsOnly: false,
      unmatchedInvoicesOnly: false,
    };
    setLocalFilters(clearedFilters);
    onClearFilters();
  };

  const hasActiveFilters =
    (localFilters.status && localFilters.status.length > 0) ||
    localFilters.vendorId ||
    localFilters.warehouseId ||
    localFilters.dateRange?.start ||
    localFilters.dateRange?.end ||
    localFilters.overdueOnly ||
    localFilters.exceptionsOnly;

  const selectedVendor = vendors.find(v => v.id === localFilters.vendorId) || null;

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
        {/* PO Status Filter */}
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <FormControl fullWidth size="small">
            <InputLabel id="status-filter-label">PO Status</InputLabel>
            <Select
              labelId="status-filter-label"
              id="status-filter"
              multiple
              value={localFilters.status || []}
              onChange={handleStatusChange}
              input={<OutlinedInput label="PO Status" />}
              renderValue={(selected) => (
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                  {selected.map((value) => (
                    <Chip key={value} label={statusLabels[value]} size="small" />
                  ))}
                </Box>
              )}
            >
              {poStatuses.map((status) => (
                <MenuItem key={status} value={status}>
                  {statusLabels[status]}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Grid>

        {/* Vendor Filter */}
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <Autocomplete
            size="small"
            options={vendors}
            getOptionLabel={(option) => option.name}
            value={selectedVendor}
            onChange={handleVendorChange}
            renderInput={(params) => (
              <TextField {...params} label="Vendor" />
            )}
          />
        </Grid>

        {/* Warehouse Filter */}
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <FormControl fullWidth size="small">
            <InputLabel id="warehouse-filter-label">Warehouse</InputLabel>
            <Select
              labelId="warehouse-filter-label"
              id="warehouse-filter"
              value={localFilters.warehouseId || ''}
              onChange={handleWarehouseChange}
              label="Warehouse"
            >
              <MenuItem value="">All Warehouses</MenuItem>
              {warehouses.map((warehouse) => (
                <MenuItem key={warehouse} value={warehouse}>
                  {warehouse}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Grid>

        {/* Start Date Filter */}
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <TextField
            fullWidth
            size="small"
            label="Due Date From"
            type="date"
            value={localFilters.dateRange?.start || ''}
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
            label="Due Date To"
            type="date"
            value={localFilters.dateRange?.end || ''}
            onChange={handleEndDateChange}
            InputLabelProps={{
              shrink: true,
            }}
          />
        </Grid>

        {/* Overdue Toggle */}
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <FormControlLabel
            control={
              <Switch
                checked={localFilters.overdueOnly || false}
                onChange={handleOverdueToggle}
                color="error"
              />
            }
            label="Overdue Only"
          />
        </Grid>

        {/* Exceptions Toggle */}
        <Grid size={{ xs: 12, sm: 6, md: 3 }}>
          <FormControlLabel
            control={
              <Switch
                checked={localFilters.exceptionsOnly || false}
                onChange={handleExceptionsToggle}
                color="warning"
              />
            }
            label="Exceptions Only"
          />
        </Grid>
      </Grid>
    </QBCard>
  );
};
