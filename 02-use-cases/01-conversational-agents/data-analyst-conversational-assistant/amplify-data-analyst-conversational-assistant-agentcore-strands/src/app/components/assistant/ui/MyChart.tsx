'use client';

import { useState, useEffect, Component, type ReactNode } from 'react';
import dynamic from 'next/dynamic';
import MarkdownRenderer from './MarkdownRenderer';

// ApexCharts requires window — load it dynamically with SSR disabled
const Chart = dynamic(() => import('react-apexcharts'), { ssr: false });

interface MyChartProps {
  caption: string;
  options: ApexCharts.ApexOptions;
  series: ApexCharts.ApexOptions['series'];
  type: string;
}

// Error boundary for chart rendering failures
class ChartErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  componentDidCatch(error: Error) {
    console.error('Chart error:', error);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="p-4 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
          Failed to render chart. Please check your chart configuration.
        </div>
      );
    }
    return this.props.children;
  }
}

export default function MyChart({ caption, options, series, type }: MyChartProps) {
  const [isVisible, setIsVisible] = useState(false);
  const [chartSeries, setChartSeries] = useState<ApexCharts.ApexOptions['series']>([]);
  const [chartOptions, setChartOptions] = useState<ApexCharts.ApexOptions>({});

  useEffect(() => {
    const t1 = setTimeout(() => setIsVisible(true), 100);
    const t2 = setTimeout(() => setChartSeries(series), 300);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [series]);

  useEffect(() => {
    const enhanced = { ...options };
    if (!enhanced.chart) enhanced.chart = {};
    enhanced.chart.zoom = { enabled: false };
    enhanced.chart.animations = { enabled: true };
    if (enhanced.chart.type === 'bar' && !enhanced.chart.stacked) {
      enhanced.dataLabels = { enabled: false };
    }
    if (enhanced.title) enhanced.title.align = 'center';
    if (enhanced.subtitle) enhanced.subtitle.align = 'center';
    setChartOptions(enhanced);
  }, [options]);

  return (
    <div
      className={`transition-all duration-800 ${
        isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-5'
      }`}
    >
      <ChartErrorBoundary>
        <Chart options={chartOptions} series={chartSeries} type={type as 'bar' | 'line' | 'pie'} height="420px" width="100%" />
      </ChartErrorBoundary>
      <div className="text-base pt-2 pb-2">
        <MarkdownRenderer content={caption} />
      </div>
    </div>
  );
}
