// Preload before Node tests/CLI help. Allows data: WASM loads, denies network.
const deny = () => { throw new Error('Offline network guard: outbound call forbidden'); };
const {EventEmitter} = require('node:events');
// Reject HTTP asynchronously like a real ClientRequest. A synchronous throw
// leaves Smithy socket-warning timers alive even after CDK synth has completed.
function deniedRequest() {
  const request = new EventEmitter();
  request.write = () => true;
  request.setTimeout = () => request;
  request.end = () => { queueMicrotask(() => request.emit('error', new Error('Offline HTTP request blocked'))); return request; };
  request.destroy = () => { request.destroyed = true; return request; };
  request.abort = request.destroy;
  return request;
}
for (const name of ['node:http', 'node:https']) {
  const module = require(name);
  module.request = deniedRequest;
  module.get = (...args) => { const request = deniedRequest(...args); request.end(); return request; };
}
const net = require('node:net');
net.connect = deny; net.createConnection = deny; net.Socket.prototype.connect = deny;
const tls = require('node:tls'); tls.connect = deny;
const fetch = globalThis.fetch;
globalThis.fetch = (url, ...args) => String(url).startsWith('data:') ? fetch(url, ...args) : deny();
