// Render an actual offline /api/trace handler response supplied by test_trace.py.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const response = JSON.parse(input);
const html = fs.readFileSync('web/index.html', 'utf8');
const esc = html.match(/const esc = .*?;\n/)[0];
const reach = html.split('const POLICY_TEXT = ')[1].split('/* ── edges')[0];
const drawer = html.split('const TRACE_COLS = ')[1].split('\nfunction closeTrace()')[0];
const body = {innerHTML: ''};
const timers = []; // the drawer re-queries a still-empty source on a timer; record, never run
const ctx = vm.createContext({
  setTimeout: (_fn, ms) => { timers.push(ms); return 0; },
  $: selector => selector === '#drBody' ? body : {dataset:{from:'cw',runTrace:'1'}},
  RUN:{run_id:response.run_id,events:[{kind:'runtime.entered',source:'runtime',status:'active'},{kind:'model.call_start',source:'runtime',status:'active'},{kind:'policy.decision',tool:'list_tickets',decision:'ALLOW',source:'runtime',status:'ok'}]},
  STATE:{persona:{customer_id:'customer-a'},policy_session:'policy-1',recorded_run:{run_id:response.run_id}},drawerEpoch:1,
  build:() => 5, traceBuild:() => 5,
  api:async () => response,
});
vm.runInContext(`${esc} const POLICY_TEXT = ${reach} const TRACE_COLS = ${drawer}\nthis.cloudTrace = cloudTrace`, ctx);
await ctx.cloudTrace(response.run_id);
const pending = response.sources.some(s => ['spans','runtime','model','gateway'].includes(s.key) &&
  (s.status === 'running' || (s.status === 'done' && !s.records.length && !s.matched)));
assert.deepEqual(timers, pending ? [8000] : []); // one re-query only while a source is still empty
assert.equal(body.innerHTML.includes('Checking again automatically (check 1 of 15, every 8 s).'), pending);
const owned = response.sources.find(s => s.key === 'gateway')?.records?.[0];
assert.ok(owned?.policies && body.innerHTML.includes(owned.policies));
if (owned.policy_ms) assert.ok(body.innerHTML.includes(owned.policy_ms));
const names = response.sources.find(s => s.key === 'spans')?.records?.map(r => r.name).filter(Boolean) || [];
for (const name of names) assert.ok(body.innerHTML.includes(name));
assert.match(body.innerHTML, /ALLOW/);
assert.match(body.innerHTML, /partial/);
assert.match(body.innerHTML, /unverified Gateway row\(s\) omitted/);
assert.match(body.innerHTML, /AgentCore Gateway APPLICATION_LOGS/);
assert.match(body.innerHTML, /Authentication \/ Identity/);
assert.match(body.innerHTML, /Policy \(agent inference\)/);
assert.doesNotMatch(body.innerHTML, /foreign-policy|secret request|@message|requestBody/);
assert.ok(!body.innerHTML.includes('<script>'));
console.log('Real offline handler → CloudWatch drawer verified partial/owned evidence: passed');
