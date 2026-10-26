// A minimal VS Code remote-protocol client (adapted from the research probe), used by the smoke
// test to talk to openvscode-server *through the box proxy*, the way the browser does behind
// AgentCore: GET /ws with the session header and the base64url "path?query" target header.

import { createRequire } from 'node:module';

const require = createRequire(new URL('../../proxy/package.json', import.meta.url));
const { WebSocket } = require('ws');

const MSG = { Regular: 1, Control: 2 };
const T = { Undefined: 0, String: 1, Buffer: 2, VSBuffer: 3, Array: 4, Object: 5, Int: 6 };

function header(type, id, length) {
  const h = Buffer.alloc(13);
  h.writeUInt8(type, 0);
  h.writeUInt32BE(id, 1);
  h.writeUInt32BE(0, 5);
  h.writeUInt32BE(length, 9);
  return h;
}

function vql(n) {
  if (n === 0) return Buffer.from([0]);
  const out = [];
  while (n !== 0) {
    let b = n & 0x7f;
    n >>>= 7;
    if (n > 0) b |= 0x80;
    out.push(b);
  }
  return Buffer.from(out);
}

function ser(d) {
  if (d === undefined) return Buffer.from([T.Undefined]);
  if (typeof d === 'string') { const b = Buffer.from(d); return Buffer.concat([Buffer.from([T.String]), vql(b.length), b]); }
  if (Array.isArray(d)) return Buffer.concat([Buffer.from([T.Array]), vql(d.length), ...d.map(ser)]);
  if (typeof d === 'number' && (d | 0) === d) return Buffer.concat([Buffer.from([T.Int]), vql(d)]);
  const b = Buffer.from(JSON.stringify(d));
  return Buffer.concat([Buffer.from([T.Object]), vql(b.length), b]);
}

function deser(buf, pos = { i: 0 }) {
  const readVql = () => {
    let value = 0;
    for (let shift = 0; ; shift += 7) {
      const b = buf[pos.i++];
      value |= (b & 0x7f) << shift;
      if ((b & 0x80) === 0) return value >>> 0;
    }
  };
  const type = buf[pos.i++];
  switch (type) {
    case T.Undefined: return undefined;
    case T.String: { const n = readVql(); const s = buf.subarray(pos.i, pos.i + n).toString(); pos.i += n; return s; }
    case T.Buffer: case T.VSBuffer: { const n = readVql(); const b = buf.subarray(pos.i, pos.i + n); pos.i += n; return b; }
    case T.Array: { const n = readVql(); const out = []; for (let k = 0; k < n; k++) out.push(deser(buf, pos)); return out; }
    case T.Object: { const n = readVql(); const o = JSON.parse(buf.subarray(pos.i, pos.i + n).toString()); pos.i += n; return o; }
    case T.Int: return readVql();
    default: throw new Error(`unknown IPC data type ${type}`);
  }
}

export function b64url(text) {
  return Buffer.from(text).toString('base64url');
}

// Connects through the proxy and completes the Management handshake. Resolves to a client with
// call(channel, command, arg) and the list of WebSocket message sizes the browser side received.
export function connect({ port, sessionId, serverRoot, commit, timeoutMs = 60_000 }) {
  const target = `${serverRoot}?reconnectionToken=${crypto.randomUUID()}&reconnection=false&skipWebSocketFrames=false`;
  const ws = new WebSocket(`ws://127.0.0.1:${port}/ws`, {
    perMessageDeflate: false,
    headers: {
      'x-amzn-bedrock-agentcore-runtime-session-id': sessionId,
      'x-amzn-bedrock-agentcore-runtime-custom-vscodepath': b64url(target),
    },
  });
  const sizes = [];
  const arrivals = [];
  let rx = Buffer.alloc(0);
  let outId = 0;
  let reqId = 0;
  const pending = new Map();
  const send = (type, body) => ws.send(Buffer.concat([header(type, type === MSG.Regular ? ++outId : 0, body.length), body]));

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('handshake timed out')), timeoutMs);
    const client = {
      sizes, arrivals,
      call(channel, command, arg) {
        const id = ++reqId;
        send(MSG.Regular, Buffer.concat([ser([100, id, channel, command]), ser(arg)]));
        return new Promise((res, rej) => pending.set(id, { res, rej }));
      },
      close: () => ws.close(),
    };
    ws.on('open', () => send(MSG.Control, Buffer.from(JSON.stringify({ type: 'auth', auth: '00000000000000000000', data: 'smoke' }))));
    ws.on('error', (err) => { clearTimeout(timer); reject(err); });
    ws.on('close', (code, reason) => {
      clearTimeout(timer);
      for (const p of pending.values()) p.rej(new Error(`closed ${code} ${reason}`));
      reject(new Error(`closed before ready: ${code} ${reason}`));
    });
    ws.on('message', (data) => {
      sizes.push(data.length);
      arrivals.push(Date.now());
      rx = Buffer.concat([rx, data]);
      while (rx.length >= 13) {
        const type = rx.readUInt8(0);
        const len = rx.readUInt32BE(9);
        if (rx.length < 13 + len) break;
        const body = rx.subarray(13, 13 + len);
        rx = rx.subarray(13 + len);
        if (type === MSG.Control) {
          const m = JSON.parse(body.toString());
          if (m.type === 'sign') send(MSG.Control, Buffer.from(JSON.stringify({ type: 'connectionType', commit, signedData: 'x', desiredConnectionType: 1 })));
          else if (m.type === 'ok') send(MSG.Regular, ser({ remoteAuthority: 'smoke', clientId: 'smoke' }));
          else if (m.type === 'error') reject(new Error(`server refused: ${m.reason}`));
        } else if (type === MSG.Regular) {
          const pos = { i: 0 };
          const head = deser(body, pos);
          if (head[0] === 200) { clearTimeout(timer); resolve(client); continue; } // Initialize
          const waiter = pending.get(head[1]);
          if (!waiter) continue;
          pending.delete(head[1]);
          const payload = pos.i < body.length ? deser(body, pos) : undefined;
          if (head[0] === 201) waiter.res(payload);
          else waiter.rej(new Error(`call failed: ${JSON.stringify(payload)?.slice(0, 300)}`));
        }
      }
    });
  });
}
