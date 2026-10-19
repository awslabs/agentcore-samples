'use client';

import { useState } from 'react';

interface TableViewProps {
  query_results: Record<string, unknown>[];
}

export default function TableView({ query_results }: TableViewProps) {
  const ROWS_PER_PAGE = 10;
  const [page, setPage] = useState(1);
  const [startColumnIndex, setStartColumnIndex] = useState(0);

  const columnEntries = Object.keys(query_results[0] || {});
  const totalColumns = columnEntries.length;
  const totalRows = query_results.length;
  const totalPages = Math.ceil(totalRows / ROWS_PER_PAGE);

  // Responsive columns — simplified with a fixed count for now
  const columnsToShow = Math.min(5, totalColumns);

  const visibleColumnKeys = columnEntries.slice(startColumnIndex, startColumnIndex + columnsToShow);
  const startRowIndex = (page - 1) * ROWS_PER_PAGE;
  const visibleRows = query_results.slice(startRowIndex, startRowIndex + ROWS_PER_PAGE);

  const formatCellValue = (value: unknown) => {
    if (value === null || value === undefined) return '-';
    if (typeof value === 'boolean') {
      return value ? '✓' : '✗';
    }
    return String(value);
  };

  return (
    <div className="w-full">
      {/* Column navigation */}
      <div className="flex justify-between items-center mb-2">
        <span className="text-xs text-gray-500">
          Showing columns {startColumnIndex + 1}-{Math.min(startColumnIndex + columnsToShow, totalColumns)} of {totalColumns}
        </span>
        <div className="flex gap-1">
          <button
            onClick={() => setStartColumnIndex((p) => Math.max(p - 1, 0))}
            disabled={startColumnIndex === 0}
            className="p-1 rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
            aria-label="Previous columns"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
          </button>
          <button
            onClick={() => setStartColumnIndex((p) => Math.min(p + 1, totalColumns - columnsToShow))}
            disabled={startColumnIndex + columnsToShow >= totalColumns}
            className="p-1 rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
            aria-label="Next columns"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse border border-gray-200 text-sm" aria-label="Query results">
          <thead>
            <tr>
              <th className="border border-gray-200 bg-gray-50/60 px-3 py-1.5 text-left font-medium w-12">#</th>
              {visibleColumnKeys.map((key) => (
                <th key={key} className="border border-gray-200 bg-gray-50/60 px-3 py-1.5 text-left font-medium max-w-[150px] truncate">
                  {key}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row, rowIndex) => (
              <tr key={rowIndex} className={rowIndex % 2 === 1 ? 'bg-gray-50/60' : ''}>
                <td className="border border-gray-200 px-3 py-1 text-right text-gray-500">{startRowIndex + rowIndex + 1}</td>
                {visibleColumnKeys.map((key) => {
                  const value = row[key];
                  const isNumber = typeof value === 'number';
                  return (
                    <td
                      key={key}
                      className={`border border-gray-200 px-3 py-1 max-w-[150px] truncate ${isNumber ? 'text-right' : 'text-left'}`}
                      title={typeof value === 'string' && value.length > 16 ? value : undefined}
                    >
                      {formatCellValue(value)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Row pagination */}
      {totalRows > ROWS_PER_PAGE && (
        <div className="flex justify-between items-center mt-3">
          <span className="text-xs text-gray-500">
            Rows {startRowIndex + 1}-{Math.min(startRowIndex + ROWS_PER_PAGE, totalRows)} of {totalRows}
          </span>
          <div className="flex gap-1">
            {Array.from({ length: totalPages }, (_, i) => (
              <button
                key={i}
                onClick={() => setPage(i + 1)}
                className={`px-2.5 py-1 text-xs rounded ${
                  page === i + 1
                    ? 'bg-purple-600 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {i + 1}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
