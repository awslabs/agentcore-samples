// Actual browser drawer function fetches a real loopback HTTP response from Lambda handler.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const [port, run] = process.argv.slice(2);
const html = fs.readFileSync('web/index.html', 'utf8');
const esc = html.match(/const esc = .*?;\n/)[0];
const policy = html.split('const POLICY_TEXT = ')[1].split('/* ── edges')[0];
const drawer = html.split('const TRACE_COLS = ')[1].split('\nfunction closeTrace()')[0];
const body = {innerHTML: ''}, panel = {dataset: {from: 'cw', runTrace: '1'}};
const ctx = vm.createContext({
  setTimeout: () => 0, // the drawer's re-query timer is recorded by cloud_trace_handler.mjs
  $: selector => selector === '#drBody' ? body : panel,
  RUN: {run_id: run, events: []}, STATE: {persona:{customer_id:'customer-a'}, policy_session:'policy-1', recorded_run:{run_id:run}}, drawerEpoch:1,
  build: () => 5, traceBuild: () => 5,
  api: async path => {
    const response = await fetch(`http://127.0.0.1:${port}${path}`);
    assert.equal(response.status, 200);
    return response.json();
  },
});
vm.runInContext(`${esc} const POLICY_TEXT = ${policy} const TRACE_COLS = ${drawer}\nthis.cloudTrace = cloudTrace;`, ctx);
await ctx.cloudTrace(run);
assert.match(body.innerHTML, /execute_tool TicketTools___list_tickets/);
assert.match(body.innerHTML, /ticketops_v4_tenant_fence-0000000000/);
assert.match(body.innerHTML, /101/);
assert.match(body.innerHTML, /ALLOW/);
assert.doesNotMatch(body.innerHTML, /secret request|@message|foreign-policy|requestBody/);
console.log('Actual Lambda HTTP → shipped browser drawer: live-schema projection passed');
