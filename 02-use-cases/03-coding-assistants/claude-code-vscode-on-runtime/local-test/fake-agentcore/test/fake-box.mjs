// A tiny stand-in for the box container (port 8080 contract), so fake-agentcore can be tested without Docker.
// It records what reached it. WebSocket text commands drive it: "big:<bytes>", "flood:<frames>",
// "pace:<frames>" (200/s), "close:<code>"; anything else is echoed with the same binary/text type.
import http from 'node:http';
import { WebSocketServer } from 'ws';

export async function startFakeBox(port, host = '127.0.0.1') {
  const seen = { invocations: [], upgrades: [], pings: 0 };
  const box = {
    seen,
    rejectUpgradeWith: 0,
    ping: { status: 'Healthy', time_of_last_update: 1700000000 },
    driftPingTimestamp: false,
  };
  const server = http.createServer((req, res) => {
    if (req.method === 'GET' && req.url === '/ping') {
      seen.pings += 1;
      if (box.driftPingTimestamp) box.ping = { ...box.ping, time_of_last_update: box.ping.time_of_last_update + 1 };
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify(box.ping));
      return;
    }
    if (req.method === 'POST' && req.url === '/invocations') {
      const chunks = [];
      req.on('data', (c) => chunks.push(c));
      req.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf8');
        seen.invocations.push({ url: req.url, headers: req.headers, body });
        let payload = {};
        try { payload = JSON.parse(body); } catch { /* not JSON */ }
        if (payload.respond) {
          res.writeHead(payload.respond, { 'content-type': 'text/plain', 'x-box-detail': 'secret-detail' });
          res.end('the box body must not reach the caller');
          return;
        }
        res.writeHead(200, { 'content-type': 'application/json', etag: '"box-etag"', 'set-cookie': 'box=1', 'cache-control': 'public, max-age=60' });
        res.end(JSON.stringify({ v: 1, ok: true, echo: payload }));
      });
      return;
    }
    res.writeHead(404);
    res.end();
  });
  const wss = new WebSocketServer({ noServer: true, perMessageDeflate: false });
  server.on('upgrade', (req, socket, head) => {
    seen.upgrades.push({ url: req.url, headers: req.headers });
    if (box.rejectUpgradeWith) {
      socket.end(`HTTP/1.1 ${box.rejectUpgradeWith} Nope\r\nContent-Length: 0\r\n\r\n`);
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => {
      ws.on('message', (data, isBinary) => {
        const text = isBinary ? '' : data.toString();
        const [cmd, arg] = text.split(':');
        if (cmd === 'big') ws.send(Buffer.alloc(Number(arg), 7), { binary: true });
        else if (cmd === 'flood') for (let i = 0; i < Number(arg); i += 1) ws.send(Buffer.from([i & 255]), { binary: true });
        else if (cmd === 'pace') {
          let i = 0;
          const t = setInterval(() => { if (i++ >= Number(arg) || ws.readyState !== 1) { clearInterval(t); return; } ws.send(Buffer.from([1]), { binary: true }); }, 5);
        } else if (cmd === 'close') ws.close(Number(arg), 'box says bye');
        else ws.send(data, { binary: isBinary });
      });
    });
  });
  await new Promise((resolve) => server.listen(port, host, resolve));
  box.url = `http://127.0.0.1:${port}`;
  box.close = () => new Promise((resolve) => {
    for (const c of wss.clients) c.terminate();
    server.closeAllConnections?.();
    server.close(() => resolve());
  });
  return box;
}
