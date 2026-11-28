const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const req = createRequire(path.resolve(__dirname, '../cdk-data-analyst-assistant-agentcore-strands/package.json'));
const ts = req('typescript');
const filename = path.resolve(__dirname, '../amplify-data-analyst-conversational-assistant-agentcore-strands/src/app/components/assistant/services/runtime-events.ts');
const moduleCopy = { exports: {} };
new Function('exports', ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText)(moduleCopy.exports);
const { RuntimeEvents } = moduleCopy.exports;
const bytes = Buffer.from('data: ' + JSON.stringify(JSON.stringify({data: 'héllo 🌎'})) + '\n\ndata: ' + JSON.stringify(JSON.stringify({data: 'done'})) + '\n\n');
for (let split = 0; split <= bytes.length; split++) {
  const parser = new RuntimeEvents();
  const decoder = new TextDecoder();
  const events = [...parser.push(decoder.decode(bytes.subarray(0, split), {stream: true})),
    ...parser.push(decoder.decode(bytes.subarray(split), {stream: true})), ...parser.push(decoder.decode())];
  parser.finish();
  assert.deepEqual(events, [{data: 'héllo 🌎'}, {data: 'done'}]);
}
assert.throws(() => new RuntimeEvents().push('data: "{\\"error\\":\\"denied\\"}"\n'), /denied/);
const truncated = new RuntimeEvents(); truncated.push('data: {'); assert.throws(() => truncated.finish(), /Truncated/);
console.log(`PASS ${bytes.length + 1} UTF-8/SSE split boundaries, rejection and truncation`);
