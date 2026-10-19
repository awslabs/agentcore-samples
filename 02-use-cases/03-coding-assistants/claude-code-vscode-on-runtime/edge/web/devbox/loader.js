// Dev box loader. Signs the person in, finds their box, waits until it is ready, then boots VS Code, or,
// on /terminal, a full-page Claude Code terminal (terminal.js) instead.
//
// VS Code's own scripts are not in the page: they are injected here only once the box reports
// vscode: "ready", because VS Code gives up after five immediate connection attempts and a cold box
// takes minutes. The terminal doesn't need VS Code: it opens once the box has its disk and has set up
// the workspace. Everything that differs between the laptop test and AWS comes from
// /devbox-config.json; the person's box comes from the provisioner, POST /api/box.
(function (root) {
  'use strict';

  const doc = root.document;
  const NONCE = doc && doc.currentScript ? doc.currentScript.nonce : '';

  const STATUS_TIMEOUT_MS = 60000;
  const POLL_MS = 2000;
  const WATCH_MS = 30000;
  const RELOAD_COUNTDOWN_S = 20;
  const REFRESH_WHEN_VISIBLE_MS = 5 * 60 * 1000;
  // A cold start takes 1–8 minutes. Past SLOW_AFTER_MS something is probably wrong (the network paused,
  // a failed capacity provider, a bad image): say so and poll once a minute. Past GIVE_UP_AFTER_MS, stop
  // on the answers that mean the box isn't coming up.
  const SLOW_AFTER_MS = 10 * 60 * 1000;
  const GIVE_UP_AFTER_MS = 20 * 60 * 1000;
  const SLOW_POLL_MS = 60000;
  const RETRYABLE = [408, 409, 424, 429, 500, 502, 503, 504];
  const OWNER_DB = 'devbox-owner';
  // VS Code web keeps settings, UI state and unsaved-editor backups in these IndexedDB databases.
  const VSCODE_DB = /^(vscode-web-db|vscode-web-state-db-.+)$/;
  // The ones whose names don't depend on the workspace or profile.
  const VSCODE_FIXED_DBS = ['vscode-web-db', 'vscode-web-state-db-global'];
  const PROJECTS = '/mnt/workspace/projects';
  const DEVICE_LOGIN = 'aws sso login --sso-session devbox --use-device-code --no-browser';
  const ASK_ADMIN = 'Ask your admin to run devbox.py status (network paused? capacity provider failed?).';
  const COLD_START = 'A cold start takes 1–8 minutes.';
  const TOO_SLOW = 'This is taking longer than a cold start should. ' + ASK_ADMIN;
  // The two ways to open the box. The page is the same; the path picks what it boots.
  const MODE_PATHS = { terminal: '/terminal', vscode: '/' };
  const MODE_LABELS = [['terminal', 'Terminal'], ['vscode', 'VS Code']];
  // xterm.js from the pinned VS Code bundle (build/pins.mjs XTERM), under SERVER_ROOT.
  const XTERM_JS = '/static/node_modules/@xterm/xterm/lib/xterm.js';
  const XTERM_CSS = '/static/node_modules/@xterm/xterm/css/xterm.css';
  // The terminal asks for a token at least this fresh on every (re)connect.
  const TERMINAL_TOKEN_MIN_MS = 5 * 60 * 1000;
  // In the terminal there is no plain shell to run the sign-in in: Claude Code's awsAuthRefresh shows it.
  const TERMINAL_HINT = 'If you aren\'t signed in to AWS in the box, Claude Code shows a sign-in URL and a code in the terminal when it opens: open the URL and enter the code.';

  class LoaderError extends Error {}

  // ---- helpers (pure; unit-tested) ----

  async function sha256Hex(text) {
    const digest = await root.crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
  }

  async function sessionIdFor(uid, generation) {
    return 'dbx-' + (await sha256Hex(uid + ':' + generation));
  }

  async function findBox(config, uid) {
    const key = await sha256Hex(uid);
    const boxes = config.boxes || {};
    return Object.prototype.hasOwnProperty.call(boxes, key) ? boxes[key] : null;
  }

  // Exponential backoff with jitter: about 1 s, 2 s, 4 s, 8 s, then 15 s.
  function backoffMs(failures, random) {
    const cap = Math.min(15000, 1000 * Math.pow(2, failures));
    return Math.round(cap / 2 + (random || Math.random)() * (cap / 2));
  }

  function invocationsUrl(agentcoreBase, runtimeArn) {
    return agentcoreBase.replace(/\/+$/, '') + '/runtimes/' + encodeURIComponent(runtimeArn) + '/invocations?qualifier=DEFAULT';
  }

  function workbenchConfig(host, config) {
    return {
      remoteAuthority: host,
      serverBasePath: '/',
      folderUri: { scheme: 'vscode-remote', authority: host, path: PROJECTS },
      webviewEndpoint: config.webviewOrigin + config.serverRoot + '/static/out/vs/workbench/contrib/webview/browser/pre',
      enableWorkspaceTrust: false,
      productConfiguration: { extensionsGallery: null },
      configurationDefaults: {
        'files.autoSave': 'afterDelay',
        'extensions.autoCheckUpdates': false,
        'extensions.autoUpdate': false,
        'telemetry.telemetryLevel': 'off',
        'task.allowAutomaticTasks': 'on',
        'claudeCode.disableLoginPrompt': true,
        'workbench.startupEditor': 'none',
        // VS Code's own AI (the Chat view with its GitHub Copilot setup, in the secondary side bar, and
        // the title-bar Chat button). Claude Code is the assistant here; its extension view keeps the
        // secondary side bar.
        'chat.disableAIFeatures': true,
        // With the Chat view gone, a first start would otherwise open an empty secondary side bar.
        'workbench.secondarySideBar.defaultVisibility': 'hidden',
      },
    };
  }

  function modeFor(pathname) {
    return pathname === MODE_PATHS.terminal ? 'terminal' : 'vscode';
  }

  // VS Code mode waits for VS Code. The terminal only needs the box set up: the disk is mounted and the
  // supervisor has prepared the home and projects folders, which it does before it starts VS Code
  // (so any VS Code state other than "waiting" means that is done, even "failed").
  function readyFor(mode) {
    if (mode === 'terminal') {
      return s => s.volume === 'mounted' && (s.vscode === 'starting' || s.vscode === 'ready' || s.vscode === 'failed');
    }
    return s => s.vscode === 'ready';
  }

  // deploy writes "terminal": true for every microVM box (AgentCore's terminal needs that compute type);
  // a box that says false (the local test's stand-in, which has no /ws/shells) gets a message instead of
  // five failed connections. A box without the field is treated as having one.
  function terminalAvailable(box) {
    return !box || box.terminal !== false;
  }

  // The page asks the provisioner for the signed-in person's box (POST /api/box, the Okta token in
  // X-Devbox-Token). On their first visit it makes the box, one step per call, answering 202 until it's ready
  // (a few minutes, once); after that it answers 200 at once. 403 says why they get no box. deps: fetch, getToken,
  // refresh, onUpdate({ status, step, message, elapsedMs }), sleep, now.
  const PROVISION_POLL_MS = 4000;
  const PROVISION_GIVE_UP_MS = 20 * 60 * 1000;
  async function provisionBox(provision, deps) {
    const started = deps.now();
    let refreshed = false;
    let failures = 0;
    for (;;) {
      let res = null;
      let body = null;
      try {
        res = await deps.fetch(provision.path, {
          method: 'POST',
          credentials: 'omit',
          cache: 'no-store',
          redirect: 'error',
          headers: {
            [provision.header || 'X-Devbox-Token']: 'Bearer ' + deps.getToken(),
            'Content-Type': 'application/json',
            Accept: 'application/json',
          },
          body: '{}',
        });
        try {
          body = await res.json();
        } catch (e) {
          body = null;
        }
      } catch (e) {
        res = null;
      }
      const status = res ? res.status : 0;
      const message = body && typeof body.message === 'string' ? body.message.slice(0, 300) : '';
      if (status === 200 && body && body.box && typeof body.box.runtimeArn === 'string') return body.box;
      if (status === 401 && !refreshed) {
        refreshed = true;
        try {
          await deps.refresh();
        } catch (e) {
          throw new LoaderError('Your sign-in was turned down (HTTP 401). Sign out and in again.');
        }
        continue;
      }
      if (status === 401 || status === 403) throw new LoaderError(message || 'You don\'t have a dev box (HTTP ' + status + ').');
      if (status === 500 && ++failures >= 3) throw new LoaderError(message || 'Setting up your dev box failed (HTTP 500).');
      if (status === 202) failures = 0;
      const elapsed = deps.now() - started;
      if (elapsed > PROVISION_GIVE_UP_MS) {
        throw new LoaderError('Your dev box is taking far longer to set up than it should. ' +
          (message || 'Ask an administrator to run devbox.py status.'));
      }
      deps.onUpdate({ status: status, step: body && body.step, message: message, elapsedMs: elapsed });
      await deps.sleep(status === 202 ? PROVISION_POLL_MS : Math.min(30000, PROVISION_POLL_MS * 2 ** Math.min(failures + 1, 3)));
    }
  }

  function formatElapsed(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }

  function formatMinutes(ms) {
    return Math.floor(Math.max(0, ms) / 60000) + ' minutes';
  }

  function describeHttp(status) {
    if (status === 401 || status === 403) return 'AgentCore turned down your sign-in for this dev box (HTTP ' + status + ').';
    if (status === 404) return 'AgentCore can\'t find this dev box (HTTP 404). Has it been deployed?';
    if (status === 400) return 'AgentCore rejected the request (HTTP 400).';
    return 'AgentCore answered HTTP ' + status + '.';
  }

  // Polls op: status until the box is ready (deps.isReady, by default VS Code ready). invoke() resolves
  // to {status, envelope} or throws on a network error or timeout; both, and 409/424/429/5xx, are
  // retried with backoff. A 401/403 gets one token refresh before it counts as a real refusal. Every
  // update carries slow: true once the wait is longer than a cold start should be; from then on it polls
  // at most once a minute. Past GIVE_UP_AFTER_MS a 424 (AgentCore gets no answer from the container), a
  // disk that still isn't attached, or VS Code still failing (when it isn't ready without it) ends the
  // wait. Other answers keep it going: they are about AgentCore or the network, and the next attempt
  // may get through.
  async function pollStatus(deps) {
    const random = deps.random || Math.random;
    const now = deps.now || Date.now;
    const pollMs = deps.pollMs === undefined ? POLL_MS : deps.pollMs;
    const isReady = deps.isReady || readyFor('vscode');
    const started = now();
    let failures = 0;
    let refreshed = false;
    for (;;) {
      let res = null;
      let failure = null;
      try {
        res = await deps.invoke();
      } catch (err) {
        failure = err || new Error('network error');
      }
      const waited = now() - started;
      const slow = waited >= SLOW_AFTER_MS;
      const late = waited >= GIVE_UP_AFTER_MS;
      const pause = ms => deps.sleep(slow ? Math.max(ms, SLOW_POLL_MS) : ms);
      if (failure) {
        deps.onUpdate({ kind: 'retry', reason: failure.name === 'AbortError' ? 'no answer within 60 s' : 'network error', slow: slow });
        await pause(backoffMs(failures++, random));
        continue;
      }
      if (res.status === 200) {
        const envelope = res.envelope;
        if (!envelope || envelope.v !== 1) throw new LoaderError('The dev box sent an unexpected reply.');
        if (envelope.ok !== true) throw new LoaderError('The dev box refused the request: ' + (envelope.error || 'unknown error'));
        failures = 0;
        deps.onUpdate({ kind: 'status', status: envelope, slow: slow });
        if (isReady(envelope)) return envelope;
        if (late && envelope.volume !== 'mounted') {
          throw new LoaderError('Your dev box has waited ' + formatMinutes(waited) + ' for its disk. ' + ASK_ADMIN);
        }
        if (late && envelope.vscode === 'failed') {
          throw new LoaderError('VS Code on your dev box keeps failing to start. Ask your admin to check the dev box\'s logs.');
        }
        await pause(pollMs);
        continue;
      }
      if ((res.status === 401 || res.status === 403) && !refreshed) {
        refreshed = true;
        try {
          await deps.refresh();
        } catch (e) {
          // The next attempt reports the refusal.
        }
        continue;
      }
      if (res.status === 424 && late) {
        throw new LoaderError('After ' + formatMinutes(waited) + ' AgentCore still gets no answer from your dev box (HTTP 424), so it isn\'t starting. ' + ASK_ADMIN);
      }
      if (RETRYABLE.indexOf(res.status) >= 0) {
        deps.onUpdate({ kind: 'retry', reason: 'AgentCore said ' + res.status, slow: slow });
        await pause(backoffMs(failures++, random));
        continue;
      }
      throw new LoaderError(describeHttp(res.status));
    }
  }

  function idbResult(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function deleteDatabase(indexedDB, name, onBlocked) {
    return new Promise((resolve, reject) => {
      const request = indexedDB.deleteDatabase(name);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
      request.onblocked = () => onBlocked && onBlocked(name);
    });
  }

  // VS Code keeps state (including unsaved buffers) in this origin's IndexedDB, keyed by folder, which
  // is the same path for everyone. If the last person to open VS Code in this browser was someone
  // else, delete that state first. Only a hash of the uid is stored.
  //
  // The per-workspace and per-profile databases can only be found with indexedDB.databases(). A
  // browser without it can't be wiped completely, so it doesn't get VS Code after someone else: a
  // partial wipe would hand over their unsaved editors. Before anyone (no owner record, which is
  // written before VS Code first starts) there is nothing to find, so it goes ahead.
  async function resetVscodeStateIfOwnerChanged(opts) {
    const indexedDB = opts.indexedDB;
    const db = await new Promise((resolve, reject) => {
      const request = indexedDB.open(OWNER_DB, 1);
      request.onupgradeneeded = () => request.result.createObjectStore('owner');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    try {
      const previous = await idbResult(db.transaction('owner', 'readonly').objectStore('owner').get('owner'));
      if (previous === opts.ownerKey) return false;
      let names;
      if (typeof indexedDB.databases === 'function') {
        names = (await indexedDB.databases()).map(d => d.name).filter(name => VSCODE_DB.test(name || ''));
      } else if (previous === undefined) {
        names = VSCODE_FIXED_DBS;
      } else {
        throw new LoaderError('This browser can\'t clear the VS Code state of the last person who used the dev box here. Use a current browser, or your own browser profile.');
      }
      for (const name of names) await deleteDatabase(indexedDB, name, opts.onBlocked);
      await idbResult(db.transaction('owner', 'readwrite').objectStore('owner').put(opts.ownerKey, 'owner'));
      return true;
    } finally {
      db.close();
    }
  }

  // ---- the page ----

  // "Terminal · VS Code": the current one as plain text, the other as a link to its path.
  function modeSwitch(mode) {
    const nav = doc.createElement('nav');
    nav.className = 'devbox-modes';
    nav.setAttribute('aria-label', 'Open the dev box as');
    MODE_LABELS.forEach((entry, i) => {
      if (i) nav.append(' · ');
      let item;
      if (entry[0] === mode) {
        item = doc.createElement('span');
        item.setAttribute('aria-current', 'page');
      } else {
        item = doc.createElement('a');
        item.href = MODE_PATHS[entry[0]];
      }
      item.className = 'devbox-mode-' + entry[0];
      item.textContent = entry[1];
      nav.appendChild(item);
    });
    return nav;
  }

  function createView() {
    const byId = id => doc.getElementById(id);
    const overlay = byId('devbox-overlay');
    let clock = null;

    function buttons(which) {
      for (const name of ['signin', 'retry', 'signout']) {
        const button = byId('devbox-' + name);
        if (button) button.hidden = !which[name];
      }
    }

    function stopClock() {
      if (clock !== null) root.clearInterval(clock);
      clock = null;
      const elapsed = byId('devbox-elapsed');
      if (elapsed) elapsed.textContent = '';
    }

    const view = {
      on(name, fn) {
        const button = byId('devbox-' + name);
        if (button) button.addEventListener('click', fn);
      },
      // Shows the Terminal · VS Code switch in the card and words the AWS hint for this mode.
      mode(mode) {
        const slot = byId('devbox-modes');
        if (slot) {
          slot.replaceChildren(modeSwitch(mode));
          slot.hidden = false;
        }
        const hint = byId('devbox-hint');
        if (hint && mode === 'terminal') hint.textContent = TERMINAL_HINT;
      },
      message(text, detail) {
        byId('devbox-status').textContent = text;
        byId('devbox-detail').textContent = detail || '';
      },
      fatal(text, detail, which) {
        stopClock();
        overlay.setAttribute('aria-busy', 'false');
        byId('devbox-hint').hidden = true;
        view.message(text, detail);
        buttons(which || { retry: true });
        const first = overlay.querySelector('button:not([hidden])');
        if (first) first.focus();
      },
      signedOut() {
        overlay.setAttribute('aria-busy', 'false');
        view.message('You are signed out.', '');
        buttons({ signin: true });
      },
      waiting(boxName) {
        const started = Date.now();
        view.message('Starting your dev box' + (boxName ? ' (' + boxName + ')' : '') + '…', COLD_START);
        buttons({ signout: true });
        const elapsed = byId('devbox-elapsed');
        clock = root.setInterval(() => {
          elapsed.textContent = 'Waiting ' + formatElapsed(Date.now() - started);
        }, 1000);
      },
      progress(update) {
        const wait = update.slow ? TOO_SLOW : COLD_START;
        if (update.kind === 'retry') {
          byId('devbox-detail').textContent = 'Still starting (' + update.reason + '). ' + wait;
          return;
        }
        const s = update.status;
        byId('devbox-states').hidden = false;
        byId('devbox-volume').textContent = s.volume === 'mounted' ? 'Attached' : 'Waiting for the disk';
        byId('devbox-vscode').textContent = { waiting: 'Waiting', starting: 'Starting', ready: 'Ready', failed: 'Failed, restarting' }[s.vscode] || String(s.vscode);
        byId('devbox-signedin').textContent = s.signedIn ? 'Signed in' : 'Not yet';
        const hint = byId('devbox-hint');
        hint.hidden = Boolean(s.signedIn);
        const login = byId('devbox-device-login');
        if (login) login.textContent = DEVICE_LOGIN;
        byId('devbox-detail').textContent = wait;
      },
      hide() {
        stopClock();
        overlay.remove();
      },
      // Banners stack at the top of the page. opts: id (one banner per id), role ('alert' by default),
      // code (shown at the end of the text, in a <code>).
      banner(text, actions, opts) {
        const options = opts || {};
        const id = options.id || 'devbox-banner';
        const old = byId(id);
        if (old) old.remove();
        let stack = byId('devbox-banners');
        if (!stack) {
          stack = doc.createElement('div');
          stack.id = 'devbox-banners';
          stack.className = 'devbox-banners';
          doc.body.appendChild(stack);
        }
        const bar = doc.createElement('div');
        bar.id = id;
        bar.className = 'devbox-banner';
        bar.setAttribute('role', options.role || 'alert');
        const label = doc.createElement('span');
        label.textContent = text;
        if (options.code) {
          const code = doc.createElement('code');
          code.textContent = options.code;
          label.append(' ', code, '.');
        }
        bar.appendChild(label);
        for (const action of actions) {
          const button = doc.createElement('button');
          button.type = 'button';
          button.textContent = action.label;
          button.addEventListener('click', action.run);
          bar.appendChild(button);
        }
        stack.appendChild(bar);
        return { bar: bar, label: label };
      },
    };
    return view;
  }

  // After VS Code (or the terminal) opens, a banner while the person isn't signed in to AWS in the box,
  // telling them where the device code shows up. It goes away once they are signed in; "Dismiss" hides
  // it until they have signed in and dropped out again.
  function awsSignInHint(view, mode) {
    const terminal = mode === 'terminal';
    let dismissed = false;
    let shown = null;
    const hide = () => {
      if (shown) shown.bar.remove();
      shown = null;
    };
    return {
      update(signedIn) {
        if (signedIn) {
          dismissed = false;
          hide();
          return;
        }
        if (dismissed || shown) return;
        const dismiss = [{ label: 'Dismiss', run: () => { dismissed = true; hide(); } }];
        shown = terminal
          ? view.banner(
            'AWS: not signed in yet in the box. Claude Code shows a sign-in URL and a code in this terminal; open the URL and enter the code.',
            dismiss,
            { id: 'devbox-aws-hint', role: 'status' },
          )
          : view.banner(
            'AWS: not signed in yet in the box. Claude Code shows a sign-in URL and a code in its terminal; open the URL and enter the code. Or open Terminal › New Terminal and run',
            dismiss,
            { id: 'devbox-aws-hint', role: 'status', code: DEVICE_LOGIN },
          );
      },
    };
  }

  function domReady() {
    if (doc.readyState !== 'loading') return Promise.resolve();
    return new Promise(resolve => doc.addEventListener('DOMContentLoaded', () => resolve(), { once: true }));
  }

  const sleep = ms => new Promise(resolve => root.setTimeout(resolve, ms));

  async function loadConfig() {
    const res = await root.fetch('/devbox-config.json', { cache: 'no-store', credentials: 'omit' });
    if (!res.ok) throw new LoaderError('/devbox-config.json answered ' + res.status);
    const config = await res.json();
    for (const key of ['commit', 'serverRoot', 'agentcoreBase', 'webviewOrigin']) {
      if (typeof config[key] !== 'string' || !config[key]) throw new LoaderError('/devbox-config.json has no ' + key);
    }
    if (!config.okta || !config.okta.issuer || !config.okta.clientId) throw new LoaderError('/devbox-config.json has no okta settings');
    return config;
  }

  async function callBox(devbox, body) {
    const controller = new AbortController();
    const timer = root.setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS);
    try {
      const res = await root.fetch(invocationsUrl(devbox.agentcoreBase, devbox.runtimeArn), {
        method: 'POST',
        mode: 'cors',
        credentials: 'omit',
        cache: 'no-store',
        redirect: 'error',
        signal: controller.signal,
        headers: {
          Authorization: 'Bearer ' + devbox.getToken(),
          'Content-Type': 'application/json',
          Accept: 'application/json',
          'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': devbox.sessionId,
        },
        body: JSON.stringify(body),
      });
      let envelope = null;
      if (res.status === 200) {
        try {
          envelope = await res.json();
        } catch (e) {
          envelope = null;
        }
      }
      return { status: res.status, envelope: envelope };
    } finally {
      root.clearTimeout(timer);
    }
  }

  // Registers /sw.js, waits until it controls this page and hands it the token (again after every
  // refresh, and whenever a restarted worker asks for it).
  async function startServiceWorker(devbox) {
    const container = root.navigator.serviceWorker;
    if (!container) throw new LoaderError('Service Workers are not available (a private window, or an insecure origin?).');
    const tokenMessage = () => ({
      type: 'devbox-token',
      token: devbox.getToken(),
      sessionId: devbox.sessionId,
      runtimeArn: devbox.runtimeArn,
      agentcoreBase: devbox.agentcoreBase,
      serverRoot: devbox.serverRoot,
    });
    container.addEventListener('message', event => {
      if (event.data && event.data.type === 'devbox-need-token' && event.source) event.source.postMessage(tokenMessage());
    });
    container.addEventListener('controllerchange', () => {
      if (container.controller) container.controller.postMessage(tokenMessage());
    });
    // addEventListener alone does not start delivery of the worker's messages.
    container.startMessages();
    await container.register('/sw.js', { scope: '/' });
    if (!container.controller) {
      await new Promise((resolve, reject) => {
        const timer = root.setTimeout(() => reject(new LoaderError('The Service Worker did not take control of the page. Reload to try again.')), 15000);
        container.addEventListener('controllerchange', () => {
          root.clearTimeout(timer);
          resolve();
        }, { once: true });
        // After a hard reload the page starts uncontrolled even though the worker is active.
        container.ready.then(registration => {
          if (!container.controller && registration.active) registration.active.postMessage({ type: 'devbox-claim' });
        });
      });
    }
    container.controller.postMessage(tokenMessage());
    return {
      postToken: () => container.controller && container.controller.postMessage(tokenMessage()),
      signedOut: () => container.controller && container.controller.postMessage({ type: 'devbox-signed-out' }),
    };
  }

  function loadElement(element) {
    return new Promise((resolve, reject) => {
      element.addEventListener('load', () => resolve(), { once: true });
      element.addEventListener('error', () => reject(new LoaderError('Could not load ' + (element.src || element.href))), { once: true });
      doc.head.appendChild(element);
    });
  }

  function moduleScript(src) {
    const script = doc.createElement('script');
    script.type = 'module';
    script.src = src;
    if (NONCE) script.nonce = NONCE;
    return script;
  }

  function classicScript(src) {
    const script = doc.createElement('script');
    script.src = src;
    if (NONCE) script.nonce = NONCE;
    return script;
  }

  // The terminal page: xterm.js (a UMD build that defines window.Terminal when there is no AMD loader,
  // which this page never has in terminal mode), its stylesheet, and our terminal.js.
  async function bootTerminal(config, opts) {
    const css = doc.createElement('link');
    css.rel = 'stylesheet';
    css.href = config.serverRoot + XTERM_CSS;
    await loadElement(css);
    await loadElement(classicScript(config.serverRoot + XTERM_JS));
    await loadElement(classicScript('/devbox/terminal.js'));
    return root.DevboxTerminal.start(opts);
  }

  async function bootWorkbench(config, view) {
    const base = config.serverRoot + '/static';
    const meta = doc.createElement('meta');
    meta.id = 'vscode-workbench-web-configuration';
    meta.setAttribute('data-settings', JSON.stringify(workbenchConfig(root.location.host, config)));
    doc.head.appendChild(meta);
    root._VSCODE_FILE_ROOT = new URL(base, root.location.origin).toString() + '/out/';
    root.performance.mark('code/willLoadWorkbenchMain');
    const css = doc.createElement('link');
    css.rel = 'stylesheet';
    css.href = base + '/out/vs/code/browser/workbench/workbench.css';
    await loadElement(css);
    // In order: workbench.js reads the messages nls.messages.js defines.
    await loadElement(moduleScript(base + '/out/nls.messages.js'));
    await loadElement(moduleScript(base + '/out/vs/code/browser/workbench/workbench.js'));
    view.hide();
  }

  // A new serverStartId means VS Code on the box restarted: this window's connection can't resume.
  // onStatus gets every good op: status answer. The terminal passes ignoreRestarts: it reconnects by
  // itself, and VS Code restarting doesn't concern it.
  function watchForRestart(startId, invoke, view, onStatus, opts) {
    const ignoreRestarts = Boolean(opts && opts.ignoreRestarts);
    let known = startId;
    let noticed = false;
    root.setInterval(async () => {
      if (noticed) return;
      let res;
      try {
        res = await invoke();
      } catch (e) {
        return;
      }
      const envelope = res.status === 200 ? res.envelope : null;
      if (!envelope || envelope.ok !== true) return;
      if (onStatus) onStatus(envelope);
      if (ignoreRestarts || !envelope.serverStartId) return;
      if (!known) {
        known = envelope.serverStartId;
        return;
      }
      if (envelope.serverStartId === known) return;
      noticed = true;
      let seconds = RELOAD_COUNTDOWN_S;
      let timer = null;
      const text = () => 'Your dev box restarted, so this window has to reload. Reloading in ' + seconds + ' s.';
      const banner = view.banner(text(), [
        { label: 'Reload now', run: () => root.location.reload() },
        {
          label: 'Not now',
          run: () => {
            root.clearInterval(timer);
            banner.label.textContent = 'Your dev box restarted. Reload this window to reconnect.';
          },
        },
      ]);
      timer = root.setInterval(() => {
        seconds -= 1;
        if (seconds <= 0) root.location.reload();
        else banner.label.textContent = text();
      }, 1000);
    }, WATCH_MS);
  }

  async function main() {
    root.performance.mark('code/didStartRenderer');
    await domReady();
    const view = createView();
    view.on('retry', () => root.location.reload());

    let config;
    try {
      config = await loadConfig();
    } catch (err) {
      view.fatal('The dev box page could not load its settings.', err.message);
      return;
    }

    let worker = null;
    const oidc = root.DevboxOidc.create({
      issuer: config.okta.issuer,
      clientId: config.okta.clientId,
      scopes: config.okta.scopes || 'openid profile email offline_access devbox',
      redirectUri: root.location.origin + '/callback',
      postLogoutRedirectUri: root.location.origin + '/',
      onSessionEnded: () => {
        view.banner('Your sign-in has ended. Reload the page to sign in again.', [
          { label: 'Reload', run: () => root.location.reload() },
        ]);
      },
    });
    const signOut = () => {
      if (worker) worker.signedOut();
      oidc.signOut();
    };
    // Okta always redirects back to /callback; the page comes back to the mode's own path from there.
    const returnTo = () => MODE_PATHS[modeFor(root.location.pathname)];
    view.on('signin', () => oidc.signIn({ promptNone: false, returnTo: returnTo() }));
    view.on('signout', signOut);

    if (oidc.consumeSignedOut()) {
      view.signedOut();
      view.mode(modeFor(root.location.pathname));
      return;
    }

    view.message('Signing in…', '');
    let redirect;
    try {
      redirect = await oidc.handleRedirect();
    } catch (err) {
      view.fatal('Sign-in failed.', err.message, { signin: true });
      return;
    }
    if (redirect === null) {
      // A fresh page has no tokens (they live only in memory). Ask Okta without prompt=none: with an Okta
      // session it answers without showing anything, otherwise it shows its sign-in page. (Real Okta answers
      // prompt=none without a session with its own 400 page instead of redirecting back with login_required.)
      await oidc.signIn({ promptNone: false, then: root.location.hash === '#signout' ? 'signout' : null, returnTo: returnTo() });
      return;
    }
    if (redirect.error) {
      if (redirect.needsInteraction) {
        await oidc.signIn({ promptNone: false, then: redirect.then, returnTo: redirect.returnTo });
        return;
      }
      view.fatal('Sign-in failed.', redirect.description || redirect.error, { signin: true });
      return;
    }
    if (redirect.then === 'signout') {
      view.message('Signing out…', '');
      signOut();
      return;
    }
    // handleRedirect has put the mode's path back in the address bar.
    const mode = modeFor(root.location.pathname);
    view.mode(mode);

    let uid = null;
    try {
      uid = oidc.accessTokenClaims().uid;
    } catch (e) {
      uid = null;
    }
    if (typeof uid !== 'string' || !uid) {
      view.fatal('Your sign-in has no uid claim.', 'The Okta authorization server must put uid in the access token.', { signout: true });
      return;
    }
    let box = null;
    if (config.provision && config.provision.path) {
      view.message('Finding your dev box…', '');
      try {
        box = await provisionBox(config.provision, {
          fetch: (url, init) => root.fetch(url, init),
          getToken: () => oidc.getAccessToken(),
          refresh: () => oidc.refresh(),
          sleep: sleep,
          now: () => Date.now(),
          onUpdate: update => view.message('Setting up your dev box…',
            (update.message ? update.message.charAt(0).toUpperCase() + update.message.slice(1) + '. ' : '') +
            'The first time takes a few minutes; after that it opens at once. ' + formatElapsed(update.elapsedMs)),
        });
      } catch (err) {
        view.fatal('No dev box for you yet.', err.message, { retry: true, signout: true });
        return;
      }
    } else {
      box = await findBox(config, uid);
    }
    if (!box) {
      view.fatal('No dev box for you.', 'Ask an administrator to create one for your account.', { signout: true });
      return;
    }
    if (mode === 'terminal' && !terminalAvailable(box)) {
      view.fatal('This dev box has no terminal.',
        'Its compute type (' + (typeof box.compute === 'string' ? box.compute.slice(0, 32) : 'unknown') + ') doesn\'t offer AgentCore\'s terminal. Open VS Code instead.',
        { signout: true });
      return;
    }
    const sessionId = await sessionIdFor(uid, box.generation);
    const devbox = Object.freeze({
      getToken: () => oidc.getAccessToken(),
      sessionId: sessionId,
      runtimeArn: box.runtimeArn,
      agentcoreBase: config.agentcoreBase,
      commit: config.commit,
      serverRoot: config.serverRoot,
      signOut: signOut,
    });
    Object.defineProperty(root, '__devbox', { value: devbox, writable: false, configurable: false });

    try {
      worker = await startServiceWorker(devbox);
    } catch (err) {
      view.fatal('The page could not start its Service Worker.', err.message);
      return;
    }
    oidc.onTokens(() => worker.postToken());
    doc.addEventListener('visibilitychange', () => {
      if (doc.visibilityState === 'visible') oidc.ensureFresh(REFRESH_WHEN_VISIBLE_MS).catch(() => {});
    });

    const invoke = () => callBox(devbox, { v: 1, op: 'status' });
    view.waiting(box.name);
    let status;
    try {
      status = await pollStatus({ invoke: invoke, refresh: () => oidc.refresh(), onUpdate: view.progress, sleep: sleep, isReady: readyFor(mode) });
    } catch (err) {
      view.fatal('The dev box is not available.', err.message, { retry: true, signout: true });
      return;
    }
    if (status.sessionId !== sessionId) {
      view.fatal('The dev box answered for a different session.', '', { signout: true });
      return;
    }

    if (mode === 'terminal') {
      // No VS Code here, so neither its build nor its browser state matters.
      view.message('Opening the terminal…', '');
      try {
        await bootTerminal(config, {
          devbox: devbox,
          generation: box.generation,
          boxName: box.name,
          getToken: async () => {
            try {
              await oidc.ensureFresh(TERMINAL_TOKEN_MIN_MS);
            } catch (e) {
              // The connection attempt reports a refused token.
            }
            return oidc.getAccessToken();
          },
          refresh: () => oidc.refresh(),
          banner: view.banner,
          modeSwitch: modeSwitch('terminal'),
          signOut: signOut,
        });
      } catch (err) {
        view.fatal('The terminal could not start.', err.message, { retry: true, signout: true });
        return;
      }
      view.hide();
      doc.title = 'Claude Code · Dev box';
      const hint = awsSignInHint(view, 'terminal');
      hint.update(status.signedIn === true);
      watchForRestart(null, invoke, view, envelope => hint.update(envelope.signedIn === true), { ignoreRestarts: true });
      return;
    }

    if (status.commit !== config.commit) {
      view.fatal('This dev box runs a different VS Code build than this page.', 'Box ' + status.commit + ', page ' + config.commit + '. Redeploy one of them.', { signout: true });
      return;
    }

    view.message('Opening VS Code…', '');
    try {
      await resetVscodeStateIfOwnerChanged({
        indexedDB: root.indexedDB,
        ownerKey: await sha256Hex('devbox-owner:' + uid),
        onBlocked: () => view.message('Close the other dev box tabs to continue.', 'This browser still has another person\'s VS Code open.'),
      });
      await bootWorkbench(config, view);
    } catch (err) {
      view.fatal('VS Code could not start.', err.message, { retry: true, signout: true });
      return;
    }
    const awsHint = awsSignInHint(view);
    awsHint.update(status.signedIn === true);
    watchForRestart(status.serverStartId, invoke, view, envelope => awsHint.update(envelope.signedIn === true));
  }

  root.DevboxLoader = Object.freeze({
    sha256Hex: sha256Hex,
    sessionIdFor: sessionIdFor,
    findBox: findBox,
    provisionBox: provisionBox,
    backoffMs: backoffMs,
    invocationsUrl: invocationsUrl,
    modeFor: modeFor,
    readyFor: readyFor,
    terminalAvailable: terminalAvailable,
    workbenchConfig: workbenchConfig,
    formatElapsed: formatElapsed,
    pollStatus: pollStatus,
    awsSignInHint: awsSignInHint,
    resetVscodeStateIfOwnerChanged: resetVscodeStateIfOwnerChanged,
  });

  if (doc && doc.getElementById) {
    main().catch(err => {
      const status = doc.getElementById('devbox-status');
      if (status) status.textContent = 'Something went wrong: ' + (err && err.message ? err.message : String(err));
    });
  }
})(typeof window !== 'undefined' ? window : globalThis);
