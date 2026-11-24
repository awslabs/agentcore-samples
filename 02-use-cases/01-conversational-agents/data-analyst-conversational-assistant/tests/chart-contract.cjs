// Offline tests of actual model-response parsing, service wiring and React boundary.
// AWS send and browser-only ApexCharts are fixtures; no model/browser/cloud claim.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const { test, after } = require('node:test');
const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'amplify-data-analyst-conversational-assistant-agentcore-strands');
const req = Module.createRequire(path.join(frontend, 'package.json'));
const ts = req('typescript');
const React = req('react');
const { renderToStaticMarkup } = req('react-dom/server');
const oldLoad = Module._load;
const oldTs = require.extensions['.ts'];
const oldTsx = require.extensions['.tsx'];
let modelResponse;
let captured;
let sent;
require.extensions['.ts'] = require.extensions['.tsx'] = (module, file) => {
  assert.ok(file.startsWith(frontend + path.sep), 'Only checked-in frontend TypeScript is compiled');
  // Compile trusted source, never model output.
  module._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText, file);
};
Module._load = function(name, parent, isMain) {
  if (name === '@/utils/aws-client') return { getAwsClient: () => ({ send: async command => {
    sent = command;
    return { body: Buffer.from(JSON.stringify({ content: [{ text: modelResponse }] })) };
  } }) };
  if (name === 'next/dynamic') return () => function ChartProbe(props) {
    captured = props;
    return React.createElement('div', { 'data-chart-probe': props.type });
  };
  return oldLoad.call(this, name, parent, isMain);
};
const directory = path.join(frontend, 'src/app/components/assistant');
const { parseChartResponse, validateChart, buildChartOptions } = require(path.join(directory, 'chart-contract.ts'));
const { generateChart } = require(path.join(directory, 'services/aws-calls.ts'));
const MyChart = require(path.join(directory, 'ui/MyChart.tsx')).default;
after(() => {
  Module._load = oldLoad;
  if (oldTs) require.extensions['.ts'] = oldTs; else delete require.extensions['.ts'];
  if (oldTsx) require.extensions['.tsx'] = oldTsx; else delete require.extensions['.tsx'];
  delete globalThis.__chartSentinel;
});
function response(type = 'bar') {
  return { has_chart: true, chart_type: type, chart_configuration: {
    title: 'Sales by region', categories: ['North America', 'Europe'],
    series: ['pie', 'donut'].includes(type) ? [120, 80] : [{ name: 'Sales', data: [120, 80] }],
    value_format: 'decimal', horizontal: false,
  }, caption: 'North America has the larger share.' };
}
function parse(value) { return parseChartResponse(JSON.stringify(value)); }
function chart(type) { return parse(response(type)); }

for (const type of ['bar', 'line', 'area', 'pie', 'donut']) {
  test(`${type}: numeric data produces application-owned rendering options`, () => {
    const parsed = chart(type);
    const options = buildChartOptions(parsed);
    assert.equal(options.type, type);
    assert.equal(options.options.chart.toolbar.show, false);
    assert.equal(options.options.chart.animations.enabled, false);
    assert.equal(options.options.tooltip.y.formatter(12.5), '12.50');
    assert.deepEqual(options.series, parsed.chart_configuration.series);
  });
}
test('built-in formats work without function strings', () => {
  for (const [format, expected] of [['decimal', '12.50'], ['integer', '13'], ['percent', '12.50%'], ['usd', '$12.50']]) {
    const input = response(); input.chart_configuration.value_format = format;
    assert.equal(buildChartOptions(parse(input)).options.yaxis.labels.formatter(12.5), expected);
  }
});
test('legacy executable formatters are rejected without running a sentinel', () => {
  globalThis.__chartSentinel = 0;
  const input = response();
  input.chart_configuration.options = { dataLabels: { formatter: '(()=>{globalThis.__chartSentinel=1;return 0})()' } };
  assert.throws(() => parse(input), /Invalid/);
  assert.equal(globalThis.__chartSentinel, 0);
});
test('unknown options, handlers, URLs and formatter expressions are refused', () => {
  for (const key of ['options', 'formatter', 'events', 'image', 'url', 'fill']) {
    const input = response(); input.chart_configuration[key] = 'untrusted';
    assert.throws(() => parse(input), /Invalid/);
  }
  const input = response(); input.chart_configuration.value_format = 'function(v){return v}';
  assert.throws(() => parse(input), /Invalid/);
});
test('prototype-sensitive keys at any level are rejected', () => {
  for (const key of ['__proto__', 'constructor', 'prototype']) {
    const input = response();
    Object.defineProperty(input.chart_configuration, key, { value: {}, enumerable: true });
    assert.throws(() => parse(input), /Invalid/);
  }
  assert.equal({}.polluted, undefined);
});
test('HTML labels and captions are rejected', () => {
  for (const field of ['title', 'categories']) {
    const input = response();
    input.chart_configuration[field] = field === 'title' ? '<img src=x onerror=alert(1)>' : ['<svg>', 'safe'];
    assert.throws(() => parse(input), /Invalid/);
  }
  const input = response(); input.caption = '<a href=javascript:alert(1)>link</a>';
  assert.throws(() => parse(input), /Invalid/);
});
test('unsupported chart types and non-boolean flags are rejected', () => {
  assert.throws(() => parse(response('heatmap')), /Invalid/);
  const input = response(); input.has_chart = 'true'; assert.throws(() => parse(input), /Invalid/);
  input.has_chart = true; input.chart_configuration.horizontal = 'false'; assert.throws(() => parse(input), /Invalid/);
  input.chart_configuration.horizontal = true; input.chart_type = 'line'; assert.throws(() => parse(input), /Invalid/);
});
test('series require finite bounded numbers and matching category counts', () => {
  for (const data of [[], [1], ['1', 2], [null, 2], [1e13, 2]]) {
    const input = response(); input.chart_configuration.series[0].data = data;
    assert.throws(() => parse(input), /Invalid/);
  }
  const input = chart(); input.chart_configuration.series[0].data[0] = Infinity;
  assert.throws(() => buildChartOptions(input), /Invalid/);
});
test('pie data is nonnegative and cannot total zero', () => {
  for (const series of [[-1, 2], [0, 0], [1]]) {
    const input = response('pie'); input.chart_configuration.series = series;
    assert.throws(() => parse(input), /Invalid/);
  }
});
test('tree, category, series and text budgets are enforced', () => {
  const many = response(); many.chart_configuration.categories = Array(201).fill('label');
  assert.throws(() => parse(many), /Invalid/);
  const series = response(); series.chart_configuration.series = Array(9).fill({ name: 'n', data: [1, 2] });
  assert.throws(() => parse(series), /Invalid/);
  const label = response(); label.chart_configuration.title = 'a'.repeat(201);
  assert.throws(() => parse(label), /Invalid/);
  const deep = response(); let cursor = deep;
  for (let i = 0; i < 10; i++) cursor = cursor.extra = {};
  assert.throws(() => parse(deep), /Invalid/);
  assert.throws(() => parseChartResponse(' '.repeat(100001)), /Invalid/);
});
test('renderer validation rejects accessors/functions/exotic prototypes without invoking them', () => {
  globalThis.__chartSentinel = 0;
  const input = chart();
  Object.defineProperty(input, 'chart_type', { get() { globalThis.__chartSentinel++; return 'bar'; }, enumerable: true });
  assert.throws(() => buildChartOptions(input), /Invalid/);
  assert.equal(globalThis.__chartSentinel, 0);
  assert.throws(() => validateChart(Object.create(chart())), /Invalid/);
  const fn = chart(); fn.chart_configuration.formatter = () => { globalThis.__chartSentinel++; };
  assert.throws(() => buildChartOptions(fn), /Invalid/);
  assert.equal(globalThis.__chartSentinel, 0);
});
test('renderer refuses sparse arrays and cycles', () => {
  const sparse = chart(); delete sparse.chart_configuration.categories[0];
  assert.throws(() => buildChartOptions(sparse), /Invalid/);
  const cyclic = chart(); cyclic.extra = cyclic;
  assert.throws(() => buildChartOptions(cyclic), /Invalid/);
});
test('no-chart response is plain bounded data', () => {
  assert.deepEqual(parse({ has_chart: false, rationale: 'No numeric data.' }), { rationale: 'No numeric data.' });
  assert.throws(() => parse({ has_chart: false, rationale: 'No chart', extra: true }), /Invalid/);
});
test('malformed or non-JSON model responses fail closed', () => {
  for (const input of ['```json\n{}\n```', '<has_chart>1</has_chart>', '{}', 'null', '[]', '{']) {
    assert.throws(() => parseChartResponse(input));
  }
});
test('factory does not mutate or retain model-owned option objects', () => {
  const input = chart(); const copy = structuredClone(input);
  const first = buildChartOptions(input); first.options.title.text = 'changed'; first.series[0].data[0] = 999;
  assert.deepEqual(input, copy);
  assert.equal(buildChartOptions(input).series[0].data[0], 120);
});
test('actual service parser feeds the actual React renderer boundary', async () => {
  modelResponse = JSON.stringify(response()); captured = undefined;
  const result = await generateChart({ text: [{ type: 'text', content: 'offline answer' }], queryResults: [] }, 'offline-model', {});
  assert.equal(sent.constructor.name, 'InvokeModelCommand');
  assert.equal(result.chart_type, 'bar');
  const html = renderToStaticMarkup(React.createElement(MyChart, { chart: result }));
  assert.match(html, /data-chart-probe="bar"/);
  assert.match(html, /North America has the larger share/);
  assert.equal(captured.options.yaxis.labels.formatter(12.5), '12.50');
  assert.equal(captured.options.chart.events, undefined);
});
test('service rejects executable model responses and renderer rejects tampered data', async () => {
  globalThis.__chartSentinel = 0;
  const input = response(); input.chart_configuration.formatter = '(()=>{globalThis.__chartSentinel=1})()';
  modelResponse = JSON.stringify(input);
  const result = await generateChart({ text: [] }, 'offline-model', {});
  assert.ok(result.rationale); assert.equal(result.chart_type, undefined);
  captured = undefined;
  const tampered = chart(); tampered.chart_configuration.options = { chart: { events: { mounted: 'code' } } };
  const html = renderToStaticMarkup(React.createElement(MyChart, { chart: tampered }));
  assert.match(html, /did not match/); assert.equal(captured, undefined);
  assert.equal(globalThis.__chartSentinel, 0);
});
