// Loaded before CDK imports in offline regression tests.
const deny = () => { throw new Error('Offline test attempted network access'); };
require('node:net').Socket.prototype.connect = deny;
require('node:tls').connect = deny;
for (const module of ['node:http', 'node:https']) {
  require(module).request = deny;
  require(module).get = deny;
}
const fetch = globalThis.fetch;
globalThis.fetch = (url, ...args) => String(url).startsWith('data:') ? fetch(url, ...args) : deny();
