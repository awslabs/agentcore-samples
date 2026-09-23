import React, { useState } from 'react';
import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
} from '@mui/material';
import { styled } from '@mui/material/styles';
import { colors } from '../../../theme/colors';

export interface QBTableColumn<T> {
  id: keyof T | string;
  label: string;
  minWidth?: number;
  align?: 'left' | 'right' | 'center';
  format?: (value: any, row: T) => React.ReactNode;
  sortable?: boolean;
}

interface QBTableProps<T> {
  columns: QBTableColumn<T>[];
  data: T[];
  onRowClick?: (row: T) => void;
}

const StyledTableContainer = styled(TableContainer)({
  boxShadow: 'none',
  border: `1px solid ${colors.border.light}`,
  borderRadius: '8px',
  overflow: 'hidden',
});

const StyledTableHead = styled(TableHead)({
  backgroundColor: colors.gray[100],
});

const StyledHeaderCell = styled(TableCell)({
  fontWeight: 600,
  fontSize: '14px',
  color: colors.text.primary,
  borderBottom: `1px solid ${colors.border.light}`,
  padding: '12px 16px',
});

const StyledTableRow = styled(TableRow, {
  shouldForwardProp: (prop) => prop !== 'clickable',
})<{ clickable?: boolean }>(({ clickable }) => ({
  '&:nth-of-type(even)': {
    backgroundColor: colors.gray[50],
  },
  '&:hover': {
    backgroundColor: colors.background.hover,
    cursor: clickable ? 'pointer' : 'default',
  },
  '&:last-child td': {
    borderBottom: 0,
  },
}));

const StyledTableCell = styled(TableCell)({
  fontSize: '14px',
  color: colors.text.primary,
  borderBottom: `1px solid ${colors.border.light}`,
  padding: '12px 16px',
});

export function QBTable<T extends Record<string, any>>({
  columns,
  data,
  onRowClick,
}: QBTableProps<T>) {
  const [orderBy, setOrderBy] = useState<keyof T | string | null>(null);
  const [order, setOrder] = useState<'asc' | 'desc'>('asc');

  const handleSort = (columnId: keyof T | string) => {
    const isAsc = orderBy === columnId && order === 'asc';
    setOrder(isAsc ? 'desc' : 'asc');
    setOrderBy(columnId);
  };

  const sortedData = React.useMemo(() => {
    if (!orderBy) return data;

    return [...data].sort((a, b) => {
      const aValue = a[orderBy];
      const bValue = b[orderBy];

      if (aValue === bValue) return 0;
      if (aValue === null || aValue === undefined) return 1;
      if (bValue === null || bValue === undefined) return -1;

      const comparison = aValue < bValue ? -1 : 1;
      return order === 'asc' ? comparison : -comparison;
    });
  }, [data, orderBy, order]);

  return (
    <StyledTableContainer>
      <Table>
        <StyledTableHead>
          <TableRow>
            {columns.map((column) => (
              <StyledHeaderCell
                key={String(column.id)}
                align={column.align || 'left'}
                style={{ minWidth: column.minWidth }}
              >
                {column.sortable !== false ? (
                  <TableSortLabel
                    active={orderBy === column.id}
                    direction={orderBy === column.id ? order : 'asc'}
                    onClick={() => handleSort(column.id)}
                  >
                    {column.label}
                  </TableSortLabel>
                ) : (
                  column.label
                )}
              </StyledHeaderCell>
            ))}
          </TableRow>
        </StyledTableHead>
        <TableBody>
          {sortedData.map((row, index) => (
            <StyledTableRow
              key={index}
              clickable={!!onRowClick}
              onClick={() => onRowClick?.(row)}
            >
              {columns.map((column) => {
                const value = row[column.id as keyof T];
                return (
                  <StyledTableCell key={String(column.id)} align={column.align || 'left'}>
                    {column.format ? column.format(value, row) : value}
                  </StyledTableCell>
                );
              })}
            </StyledTableRow>
          ))}
        </TableBody>
      </Table>
    </StyledTableContainer>
  );
}
