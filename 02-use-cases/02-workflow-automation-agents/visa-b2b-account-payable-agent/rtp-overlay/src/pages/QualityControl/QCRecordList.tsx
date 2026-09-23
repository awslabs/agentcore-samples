/**
 * QC Record List Page
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { mockDataService, type QCRecord } from '../../services/mockDataService';
import QCStatusBadge from '../../components/common/QCStatusBadge';
import { oilGasTheme } from '../../styles/oilGasTheme';

const QCRecordList: React.FC = () => {
  const navigate = useNavigate();
  const [qcRecords] = useState<QCRecord[]>(mockDataService.getQCRecords());

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  };

  const passCount = qcRecords.filter(qc => qc.overallStatus === 'PASS').length;
  const failCount = qcRecords.filter(qc => qc.overallStatus === 'FAIL').length;

  return (
    <div>
      <div style={{ marginBottom: oilGasTheme.spacing.xl, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: oilGasTheme.typography.fontSize['3xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.text, margin: 0, marginBottom: oilGasTheme.spacing.xs }}>
            Quality Control Records
          </h1>
          <p style={{ fontSize: oilGasTheme.typography.fontSize.base, color: oilGasTheme.colors.textMuted, margin: 0 }}>
            Laboratory tests and field measurements
          </p>
        </div>
        <button
          onClick={() => navigate('/quality-control/create')}
          style={{ padding: `${oilGasTheme.spacing.md} ${oilGasTheme.spacing.lg}`, backgroundColor: oilGasTheme.colors.primary, color: '#ffffff', border: 'none', borderRadius: oilGasTheme.borderRadius.md, fontSize: oilGasTheme.typography.fontSize.base, fontWeight: oilGasTheme.typography.fontWeight.semibold, cursor: 'pointer' }}
        >
          + Create QC Record
        </button>
      </div>

      {/* Summary Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: oilGasTheme.spacing.lg, marginBottom: oilGasTheme.spacing.xl }}>
        <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.lg, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm }}>
          <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.sm }}>Total Tests</div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize['3xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.primary }}>{qcRecords.length}</div>
        </div>
        <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.lg, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm }}>
          <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.sm }}>Passed</div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize['3xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.success }}>{passCount}</div>
        </div>
        <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.lg, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm }}>
          <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.sm }}>Failed</div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize['3xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.danger }}>{failCount}</div>
        </div>
        <div style={{ backgroundColor: oilGasTheme.colors.surface, padding: oilGasTheme.spacing.lg, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm }}>
          <div style={{ fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.textMuted, marginBottom: oilGasTheme.spacing.sm }}>Pass Rate</div>
          <div style={{ fontSize: oilGasTheme.typography.fontSize['3xl'], fontWeight: oilGasTheme.typography.fontWeight.bold, color: oilGasTheme.colors.success }}>{Math.round((passCount / qcRecords.length) * 100)}%</div>
        </div>
      </div>

      <div style={{ backgroundColor: oilGasTheme.colors.surface, borderRadius: oilGasTheme.borderRadius.lg, boxShadow: oilGasTheme.shadows.sm, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ backgroundColor: oilGasTheme.colors.background, borderBottom: `2px solid ${oilGasTheme.colors.border}` }}>
              <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>QC Number</th>
              <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>PO Number</th>
              <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Material</th>
              <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Test Date</th>
              <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Inspector</th>
              <th style={{ padding: oilGasTheme.spacing.md, textAlign: 'left', fontSize: oilGasTheme.typography.fontSize.sm, fontWeight: oilGasTheme.typography.fontWeight.semibold, color: oilGasTheme.colors.text, textTransform: 'uppercase' }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {qcRecords.map((qc, index) => (
              <tr
                key={qc.id}
                onClick={() => navigate(`/quality-control/${qc.id}`)}
                style={{ borderBottom: `1px solid ${oilGasTheme.colors.border}`, backgroundColor: index % 2 === 0 ? oilGasTheme.colors.surface : oilGasTheme.colors.background, cursor: 'pointer' }}
              >
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.primary, fontWeight: oilGasTheme.typography.fontWeight.semibold }}>{qc.qcNumber}</td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.text }}>{qc.poNumber}</td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.text }}>{qc.materialDescription}</td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.text }}>{formatDate(qc.testDate)}</td>
                <td style={{ padding: oilGasTheme.spacing.md, fontSize: oilGasTheme.typography.fontSize.sm, color: oilGasTheme.colors.text }}>{qc.inspectorName}</td>
                <td style={{ padding: oilGasTheme.spacing.md }}><QCStatusBadge status={qc.overallStatus} size="sm" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default QCRecordList;
