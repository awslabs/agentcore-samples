/** Model output is data, never ApexCharts options, HTML, URLs or executable code. */
export type ChartType = 'bar' | 'line' | 'area' | 'pie' | 'donut';
export type ValueFormat = 'decimal' | 'integer' | 'percent' | 'usd';
export interface NumericSeries { name: string; data: number[] }
export interface ChartConfiguration {
  title: string;
  categories: string[];
  series: NumericSeries[] | number[];
  value_format: ValueFormat;
  horizontal: boolean;
}
export interface ChartConfig {
  chart_type: ChartType;
  chart_configuration: ChartConfiguration;
  caption: string;
}
export interface ChartRationale { rationale: string }

const MAX_RESPONSE = 100_000;
const MAX_CATEGORIES = 200;
const MAX_SERIES = 8;
const chartTypes: readonly string[] = ['bar', 'line', 'area', 'pie', 'donut'];
const valueFormats: readonly string[] = ['decimal', 'integer', 'percent', 'usd'];
const forbiddenKeys = new Set(['__proto__', 'prototype', 'constructor']);
const invalid = (): never => { throw new Error('Invalid or unsupported chart data'); };

// Also used at the renderer boundary: reject accessors, functions, exotic
// prototypes and excessive trees before reading properties or spreading objects.
function guardTree(value: unknown, depth = 0, budget = { nodes: 0 }): void {
  if (++budget.nodes > 5000 || depth > 8) invalid();
  if (value === null || typeof value === 'boolean') return;
  if (typeof value === 'string') { if (value.length > MAX_RESPONSE) invalid(); return; }
  if (typeof value === 'number') { if (!Number.isFinite(value)) invalid(); return; }
  if (typeof value !== 'object') invalid();
  const object = value as object;
  const array = Array.isArray(object);
  if (Object.getPrototypeOf(object) !== (array ? Array.prototype : Object.prototype)) invalid();
  if (array && ((object as unknown[]).length > MAX_CATEGORIES || Object.keys(object).length !== (object as unknown[]).length)) invalid();
  for (const key of Reflect.ownKeys(object)) {
    if (typeof key !== 'string' || forbiddenKeys.has(key)) invalid();
    if (array && key === 'length') continue;
    if (array && !/^(0|[1-9][0-9]*)$/.test(key as string)) invalid();
    const descriptor = Object.getOwnPropertyDescriptor(object, key)!;
    if (!('value' in descriptor) || !descriptor.enumerable) invalid();
    guardTree(descriptor.value, depth + 1, budget);
  }
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid();
  return value as Record<string, unknown>;
}
function keys(value: Record<string, unknown>, allowed: string[]): void {
  if (Object.keys(value).some(key => !allowed.includes(key))) invalid();
}
function text(value: unknown, max: number, empty = false): string {
  if (typeof value !== 'string' || value.length > max || (!empty && !value.trim())) invalid();
  const result = value as string;
  // Plain labels only. Do not feed markup/control characters to chart-library
  // HTML/SVG sinks; captions are rendered as React text, not generated Markdown.
  if (/[<>]/.test(result) || [...result].some(c => c.charCodeAt(0) < 32)) invalid();
  return result;
}
function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || Math.abs(value) > 1e12) invalid();
  return value as number;
}

export function validateChart(value: unknown): ChartConfig {
  guardTree(value);
  const chart = record(value);
  keys(chart, ['chart_type', 'chart_configuration', 'caption']);
  if (typeof chart.chart_type !== 'string' || !chartTypes.includes(chart.chart_type)) invalid();
  const type = chart.chart_type as ChartType;
  const config = record(chart.chart_configuration);
  keys(config, ['title', 'categories', 'series', 'value_format', 'horizontal']);
  if (!Array.isArray(config.categories) || !config.categories.length || config.categories.length > MAX_CATEGORIES) invalid();
  const categories = (config.categories as unknown[]).map(label => text(label, 120));
  const title = config.title === undefined ? '' : text(config.title, 200, true);
  const format = config.value_format ?? 'decimal';
  if (typeof format !== 'string' || !valueFormats.includes(format)) invalid();
  const horizontal = config.horizontal ?? false;
  if (typeof horizontal !== 'boolean' || (horizontal && type !== 'bar')) invalid();
  if (!Array.isArray(config.series) || !config.series.length) invalid();
  let series: NumericSeries[] | number[];
  if (type === 'pie' || type === 'donut') {
    series = (config.series as unknown[]).map(number);
    if (series.length !== categories.length || series.some(v => v < 0) || !series.some(v => v > 0)) invalid();
  } else {
    if ((config.series as unknown[]).length > MAX_SERIES) invalid();
    series = (config.series as unknown[]).map(item => {
      const row = record(item);
      keys(row, ['name', 'data']);
      if (!Array.isArray(row.data) || row.data.length !== categories.length) invalid();
      return { name: text(row.name, 120), data: (row.data as unknown[]).map(number) };
    });
  }
  return {
    chart_type: type,
    chart_configuration: { title, categories, series, value_format: format as ValueFormat, horizontal: horizontal as boolean },
    caption: text(chart.caption, 400),
  };
}

export function parseChartResponse(response: unknown): ChartConfig | ChartRationale {
  if (typeof response !== 'string' || response.length > MAX_RESPONSE) invalid();
  const value: unknown = JSON.parse(response as string);
  guardTree(value);
  const result = record(value);
  if (result.has_chart === false) {
    keys(result, ['has_chart', 'rationale']);
    return { rationale: text(result.rationale, 200) };
  }
  if (result.has_chart !== true) invalid();
  keys(result, ['has_chart', 'chart_type', 'chart_configuration', 'caption']);
  return validateChart({ chart_type: result.chart_type, chart_configuration: result.chart_configuration, caption: result.caption });
}

const numeric = (value: number, render: (value: number) => string): string => Number.isFinite(value) ? render(value) : '';
const formatters = Object.freeze({
  decimal: (value: number) => numeric(value, v => v.toFixed(2)),
  integer: (value: number) => numeric(value, v => Math.round(v).toString()),
  percent: (value: number) => numeric(value, v => `${v.toFixed(2)}%`),
  usd: (value: number) => numeric(value, v => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v)),
});

/** Only this application-owned factory supplies executable callbacks to ApexCharts. */
export function buildChartOptions(value: unknown): {
  type: ChartType; caption: string; options: ApexCharts.ApexOptions; series: ApexCharts.ApexOptions['series'];
} {
  const chart = validateChart(value);
  const data = chart.chart_configuration;
  const formatter = formatters[data.value_format];
  const circular = chart.chart_type === 'pie' || chart.chart_type === 'donut';
  const options: ApexCharts.ApexOptions = {
    chart: { type: chart.chart_type, animations: { enabled: false }, toolbar: { show: false }, zoom: { enabled: false } },
    colors: ['#008FFB', '#00E396', '#FEB019', '#775DD0', '#FF4560', '#546E7A', '#26A69A', '#D4526E'],
    title: { text: data.title, align: 'center' },
    legend: { position: 'bottom' },
    dataLabels: { enabled: circular, formatter: circular ? formatters.percent : formatter },
    tooltip: { y: { formatter } },
  };
  if (circular) options.labels = data.categories;
  else {
    options.xaxis = { categories: data.categories };
    options.yaxis = { labels: { formatter } };
    if (chart.chart_type === 'bar') options.plotOptions = { bar: { horizontal: data.horizontal } };
    else options.stroke = { curve: 'straight' };
  }
  return { type: chart.chart_type, caption: chart.caption, options, series: data.series };
}
