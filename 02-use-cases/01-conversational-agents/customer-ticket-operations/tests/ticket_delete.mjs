// Exercise the console's actual ticket renderer/delete handler with an offline API stub.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const html = fs.readFileSync('web/index.html', 'utf8');
const esc = html.match(/const esc = .*?;\n/)[0];
const renderer = html.split('function renderTickets(st){')[1].split('// V3+: each held')[0];
const handler = html.split('async function deleteTicket(ticketId){')[1].split('async function resetDemo()')[0];
let confirmed = false, calls = [], notices = [];
const tickets = {innerHTML: ''};
const initial = [
  {ticket_id:'A-41', origin:'seed', submitted_by:'customer', subject:'Seed', body:'safe', status:'OPEN'},
  {ticket_id:'A-90', origin:'form', submitted_by:'customer', subject:'<script>alert(1)</script>', body:'injected', status:'OPEN'},
];
const state = {tickets: initial, last_run:null, store_comparison:{}, approvals:[], persona:{customer_id:'customer-a'}};
const context = vm.createContext({
  STATE: state, RUN:null, ASKS:new Set(), polling:null, console,
  $: id => id === '#tickets' ? tickets : ({textContent:'', hidden:true}),
  build:() => 5, custName:() => 'Region A', regionName:() => 'Region A', who:() => 'alice',
  renderApprovals:() => {}, confirm:() => confirmed,
  api:async (path, body) => {calls.push([path, body]); assert.equal(path, '/api/tickets/delete'); return {deleted:'A-90', source:'operator'};},
  refresh:async () => {state.tickets = [initial[0]]; vm.runInContext('renderTickets({effects:new Set()})', context);},
  toast: msg => notices.push(msg)
});
vm.runInContext(`${esc} function renderTickets(st){${renderer} async function deleteTicket(ticketId){${handler}\nthis.ui={renderTickets,deleteTicket};`, context);
context.ui.renderTickets({effects:new Set()});
assert.match(tickets.innerHTML, /data-delete="A-90" aria-label="Delete portal ticket A-90"/);
assert.doesNotMatch(tickets.innerHTML, /data-delete="A-41"/);
assert.ok(!tickets.innerHTML.includes('<script>'));
assert.match(tickets.innerHTML, /&#60;script&#62;/);
await context.ui.deleteTicket('A-90');
assert.equal(calls.length, 0);
confirmed = true;
state.busy = true;
context.ui.renderTickets({effects:new Set()});
assert.match(tickets.innerHTML, /data-delete="A-90"[^>]* disabled/);
await context.ui.deleteTicket('A-90');
assert.equal(calls.length, 0);
state.busy = false;
await context.ui.deleteTicket('A-90');
assert.equal(calls.length, 1);
assert.equal(calls[0][1].ticket_id, 'A-90');
assert.doesNotMatch(tickets.innerHTML, /data-delete="A-90"/);
assert.match(notices[0], /Operator deleted portal ticket A-90/);
console.log('Ticket delete X, escaping, confirm cancel and success: passed');
