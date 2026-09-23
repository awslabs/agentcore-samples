// Coding Agents — Split-Pane Comparison Frontend
// Supports 2-panel and 4-panel layouts.
// Each pane can run in command mode (SSE) or TUI mode (WebSocket PTY).

const ALL_PANES = ['tl', 'tr', 'bl', 'br'];
let activePanes = ['tl'];
let currentLayout = 1;

const state = {};
ALL_PANES.forEach(id => {
  state[id] = { mode: 'command', ws: null, term: null, fitAddon: null };
});

// Single session ID shared across all panes and modes (command + TUI)
function generateSessionId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  return `coding-agents-${Date.now()}-${hex}`;
}
let SESSION_ID = generateSessionId();

// Display session ID in the top bar
const sessionDisplay = document.getElementById('session-display');
function updateSessionDisplay() {
  sessionDisplay.textContent = SESSION_ID;
}
updateSessionDisplay();

sessionDisplay.addEventListener('click', () => {
  navigator.clipboard.writeText(SESSION_ID);
  sessionDisplay.textContent = 'Copied!';
  setTimeout(updateSessionDisplay, 1500);
});

document.getElementById('session-renew').addEventListener('click', () => {
  SESSION_ID = generateSessionId();
  updateSessionDisplay();
});

const TERM_THEME = {
  background: '#0d1117',
  foreground: '#c9d1d9',
  cursor: '#58a6ff',
  selectionBackground: '#264f78',
};

// ── Helpers ──────────────────────────────────────────────────

function getSelectedArn(paneId) {
  const select = document.getElementById(`select-${paneId}`);
  const option = select.options[select.selectedIndex];
  return option.dataset.arn || '';
}

function getSelectedAgent(paneId) {
  const select = document.getElementById(`select-${paneId}`);
  return select.value;
}

function setStatus(paneId, status) {
  const el = document.getElementById(`status-${paneId}`);
  el.textContent = status;
  el.className = `pane-status ${status}`;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function stripAnsi(text) {
  return text.replace(/\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '');
}

// ── Layout Toggle ────────────────────────────────────────────

document.getElementById('layout-1').addEventListener('click', () => setLayout(1));
document.getElementById('layout-2').addEventListener('click', () => setLayout(2));
document.getElementById('layout-4').addEventListener('click', () => setLayout(4));

function setLayout(n) {
  currentLayout = n;
  const container = document.getElementById('split-container');

  document.querySelectorAll('.btn-layout').forEach(b => b.classList.remove('active'));
  document.getElementById(`layout-${n}`).classList.add('active');

  if (n === 1) {
    container.className = 'split-container layout-1';
    activePanes = ['tl'];
    document.getElementById('pane-tl').style.display = '';
    document.getElementById('pane-tr').style.display = 'none';
    document.getElementById('pane-bl').style.display = 'none';
    document.getElementById('pane-br').style.display = 'none';
  } else if (n === 2) {
    container.className = 'split-container layout-2';
    activePanes = ['tl', 'tr'];
    document.getElementById('pane-tl').style.display = '';
    document.getElementById('pane-tr').style.display = '';
    document.getElementById('pane-bl').style.display = 'none';
    document.getElementById('pane-br').style.display = 'none';
  } else {
    container.className = 'split-container layout-4';
    activePanes = ['tl', 'tr', 'bl', 'br'];
    ALL_PANES.forEach(id => {
      document.getElementById(`pane-${id}`).style.display = '';
    });
  }

  // Re-fit any active terminals
  ALL_PANES.forEach(id => {
    if (state[id].fitAddon) state[id].fitAddon.fit();
  });
}

// Update model placeholder when agent selection changes
function updateModelPlaceholder(paneId) {
  const agentKey = getSelectedAgent(paneId);
  const modelInput = document.getElementById(`model-${paneId}`);
  if (modelInput && window.AGENTS[agentKey]) {
    modelInput.placeholder = window.AGENTS[agentKey].default_model || 'model';
    modelInput.value = '';
  }
}

// Set default selections for 4-panel mode
function setDefaultSelections() {
  const agents = Object.keys(window.AGENTS || {});
  const selects = ['tl', 'tr', 'bl', 'br'];
  selects.forEach((id, i) => {
    const select = document.getElementById(`select-${id}`);
    if (select && agents[i]) {
      select.value = agents[i];
    }
    updateModelPlaceholder(id);
  });
}
setDefaultSelections();

// Listen for agent selection changes to update model placeholder
ALL_PANES.forEach(id => {
  document.getElementById(`select-${id}`).addEventListener('change', () => updateModelPlaceholder(id));
});

// ── Command Mode: run via /api/invoke (SSE streaming) ────────

function getSelectedModel(paneId) {
  const modelInput = document.getElementById(`model-${paneId}`);
  return modelInput ? modelInput.value.trim() : '';
}

async function runCommand(paneId, command) {
  const arn = getSelectedArn(paneId);
  const outputEl = document.getElementById(`output-${paneId}`);

  if (!arn) {
    outputEl.innerHTML = '<div class="error-text">No runtime ARN for this agent. Deploy it first.</div>';
    setStatus(paneId, 'error');
    return;
  }
  if (!command) return;

  outputEl.innerHTML = `<div class="cmd-line">$ ${escapeHtml(command.substring(0, 200))}</div>`;
  setStatus(paneId, 'running');

  try {
    const agentType = getSelectedAgent(paneId);
    const model = getSelectedModel(paneId);
    const res = await fetch('/api/invoke', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        runtime_arn: arn,
        session_id: SESSION_ID,
        command: command,
        agent_type: agentType,
        model: model,
        mode: 'prompt',
        timeout: 900,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      outputEl.innerHTML += `<div class="error-text">${escapeHtml(err.error || 'Request failed')}</div>`;
      setStatus(paneId, 'error');
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6);
        if (payload === '[DONE]') continue;
        let parsed;
        try { parsed = JSON.parse(payload); } catch { continue; }

        if (parsed.type === 'stdout') {
          const span = document.createElement('span');
          span.className = 'stdout';
          span.textContent = stripAnsi(parsed.text);
          outputEl.appendChild(span);
        } else if (parsed.type === 'stderr') {
          const span = document.createElement('span');
          span.className = 'stderr';
          span.textContent = stripAnsi(parsed.text);
          outputEl.appendChild(span);
        } else if (parsed.type === 'exit') {
          const cls = parsed.code === 0 ? 'success' : 'failure';
          outputEl.innerHTML += `<div class="exit-line ${cls}">Exit: ${parsed.code}</div>`;
          setStatus(paneId, parsed.code === 0 ? 'done' : 'error');
        } else if (parsed.type === 'error') {
          outputEl.innerHTML += `<div class="error-text">${escapeHtml(parsed.text)}</div>`;
          setStatus(paneId, 'error');
        }
        outputEl.scrollTop = outputEl.scrollHeight;
      }
    }

    const statusEl = document.getElementById(`status-${paneId}`);
    if (statusEl.textContent === 'running') setStatus(paneId, 'done');
  } catch (e) {
    outputEl.innerHTML += `<div class="error-text">Error: ${escapeHtml(e.message)}</div>`;
    setStatus(paneId, 'error');
  }
}

// ── TUI Mode: full interactive terminal via WebSocket ────────

function connectTUI(paneId) {
  const arn = getSelectedArn(paneId);
  if (!arn) {
    setStatus(paneId, 'error');
    return;
  }

  state[paneId].mode = 'tui';
  document.getElementById(`output-${paneId}`).classList.add('hidden');
  const termContainer = document.getElementById(`terminal-${paneId}`);
  termContainer.classList.remove('hidden');

  const term = new Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: "'SF Mono', 'Menlo', 'Cascadia Code', monospace",
    theme: TERM_THEME,
  });
  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  termContainer.innerHTML = '';
  term.open(termContainer);
  fitAddon.fit();

  state[paneId].term = term;
  state[paneId].fitAddon = fitAddon;

  setStatus(paneId, 'connecting');
  document.getElementById(`tui-${paneId}`).disabled = true;
  const disconnectBtn = document.getElementById(`disconnect-${paneId}`);

  const model = getSelectedModel(paneId);
  const agentType = getSelectedAgent(paneId);
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  let wsUrl = `${wsProtocol}//${window.location.host}/ws/proxy/${SESSION_ID}?arn=${encodeURIComponent(arn)}&agent_type=${encodeURIComponent(agentType)}`;
  if (model) wsUrl += `&model=${encodeURIComponent(model)}`;

  const ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';
  state[paneId].ws = ws;

  ws.onopen = () => {
    setStatus(paneId, 'connected');
    disconnectBtn.disabled = false;
    term.write('\r\n\x1b[32m● Connected to AgentCore Runtime\x1b[0m\r\n');
    const { cols, rows } = term;
    const resizePayload = JSON.stringify({ width: cols, height: rows });
    const encoded = new TextEncoder().encode(resizePayload);
    const frame = new Uint8Array(1 + encoded.length);
    frame[0] = 0x04;
    frame.set(encoded, 1);
    ws.send(frame.buffer);
  };

  ws.onmessage = (event) => {
    if (!(event.data instanceof ArrayBuffer)) return;
    const data = new Uint8Array(event.data);
    if (data.length < 1) return;
    const channel = data[0];
    const payload = data.slice(1);
    if (channel === 0x01 || channel === 0x02) {
      term.write(new TextDecoder().decode(payload));
    } else if (channel === 0x03) {
      try {
        const status = JSON.parse(new TextDecoder().decode(payload));
        if (status.error) {
          term.write(`\r\n\x1b[31m${status.error}\x1b[0m\r\n`);
          setStatus(paneId, 'error');
        }
      } catch {}
    } else if (channel === 0xFF) {
      term.write('\r\n\x1b[33m● Connection closed\x1b[0m\r\n');
      disconnectTUI(paneId);
    }
  };

  ws.onclose = () => {
    setStatus(paneId, 'disconnected');
    document.getElementById(`tui-${paneId}`).disabled = false;
    disconnectBtn.disabled = true;
  };

  ws.onerror = () => {
    term.write('\r\n\x1b[31m● WebSocket error\x1b[0m\r\n');
    setStatus(paneId, 'error');
  };

  term.onData(data => {
    if (ws.readyState === WebSocket.OPEN) {
      const payload = new TextEncoder().encode(data);
      const frame = new Uint8Array(1 + payload.length);
      frame[0] = 0x00;
      frame.set(payload, 1);
      ws.send(frame.buffer);
    }
  });

  term.onResize(({ cols, rows }) => {
    if (ws.readyState === WebSocket.OPEN) {
      const resizePayload = JSON.stringify({ width: cols, height: rows });
      const encoded = new TextEncoder().encode(resizePayload);
      const frame = new Uint8Array(1 + encoded.length);
      frame[0] = 0x04;
      frame.set(encoded, 1);
      ws.send(frame.buffer);
    }
  });

  window.addEventListener('resize', () => {
    if (state[paneId].fitAddon) state[paneId].fitAddon.fit();
  });
}

function disconnectTUI(paneId) {
  const s = state[paneId];
  if (s.ws) {
    if (s.ws.readyState === WebSocket.OPEN) {
      const closeFrame = new Uint8Array([0xFF]);
      s.ws.send(closeFrame.buffer);
      s.ws.close();
    }
    s.ws = null;
  }
  if (s.term) {
    s.term.dispose();
    s.term = null;
    s.fitAddon = null;
  }
  s.mode = 'command';
  document.getElementById(`terminal-${paneId}`).classList.add('hidden');
  document.getElementById(`output-${paneId}`).classList.remove('hidden');
  setStatus(paneId, 'idle');
  document.getElementById(`tui-${paneId}`).disabled = false;
  document.getElementById(`disconnect-${paneId}`).disabled = true;
}

// ── Event Listeners ──────────────────────────────────────────

ALL_PANES.forEach(id => {
  document.getElementById(`tui-${id}`).addEventListener('click', () => connectTUI(id));
  document.getElementById(`disconnect-${id}`).addEventListener('click', () => disconnectTUI(id));
  document.getElementById(`clear-${id}`).addEventListener('click', () => {
    const outputEl = document.getElementById(`output-${id}`);
    outputEl.innerHTML = '<div class="empty-state">Select an agent and enter a prompt...</div>';
    setStatus(id, 'idle');
  });
});

// TUI All — connect all visible panes at once
const tuiAllBtn = document.getElementById('tui-all-btn');
const discAllBtn = document.getElementById('disc-all-btn');

tuiAllBtn.addEventListener('click', () => {
  activePanes.forEach(paneId => {
    if (state[paneId].mode !== 'tui') {
      connectTUI(paneId);
    }
  });
  tuiAllBtn.disabled = true;
  discAllBtn.disabled = false;
});

// Disconnect All — disconnect all visible panes
discAllBtn.addEventListener('click', () => {
  activePanes.forEach(paneId => {
    if (state[paneId].mode === 'tui') {
      disconnectTUI(paneId);
    }
  });
  tuiAllBtn.disabled = false;
  discAllBtn.disabled = true;
});
