'use client';

import { useMemo, Component, type ReactNode } from 'react';
import dynamic from 'next/dynamic';
import { buildChartOptions, type ChartConfig } from '../chart-contract';

// Only validated data reaches the application-owned ApexCharts option factory.
const Chart = dynamic(() => import('react-apexcharts'), { ssr: false });

class ChartErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError() { return { hasError: true }; }
  componentDidCatch() { console.error('Chart rendering failed'); }
  render() {
    if (this.state.hasError) return <p role="status">Unable to render this chart.</p>;
    return this.props.children;
  }
}

export default function MyChart({ chart }: { chart: ChartConfig }) {
  const rendered = useMemo(() => {
    try { return buildChartOptions(chart); }
    catch { return null; }
  }, [chart]);
  if (!rendered) return <p role="status">Chart data did not match the supported format.</p>;
  return (
    <div>
      <ChartErrorBoundary>
        <Chart options={rendered.options} series={rendered.series} type={rendered.type} height="420px" width="100%" />
      </ChartErrorBoundary>
      <p className="text-base pt-2 pb-2">{rendered.caption}</p>
    </div>
  );
}
