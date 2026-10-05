import { beforeEach, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { XTERM } from '../build/pins.mjs';
import { EDGE } from './fixture.mjs';
import { makeSandbox, runScript } from './sandbox.mjs';

const plain = value => JSON.parse(JSON.stringify(value));
const sha = text => createHash('sha256').update(text).digest('hex');

let L;

beforeEach(() => {
  // No document: the loader only defines its helpers and does not start.
  const ctx = makeSandbox({ setTimeout, clearTimeout });
  runScript(ctx, 'devbox/loader.js');
  L = ctx.DevboxLoader;
});

// The page asks POST /api/box for the signed-in person's box; the first visit makes it.
describe('provisionBox (POST /api/box)', () => {
  const BOX = { name: 'gracehopper1a2b3c', runtimeArn: 'arn:aws:bedrock-agentcore:us-east-1:1:runtime/devbox_vm_gracehopper1a2b3c-x', generation: 1 };
  const reply = (status, body) => ({ status, json: async () => body });
  function deps(replies, extra = {}) {
    const calls = [], updates = [];
    let clock = 0;
    return {
      calls, updates,
      fetch: async (url, init) => { calls.push({ url, init }); const r = replies.shift(); if (r instanceof Error) throw r; return r; },
      getToken: () => 'tok',
      refresh: async () => { calls.push({ refresh: true }); },
      onUpdate: u => updates.push(u),
      sleep: async ms => { clock += ms; },
      now: () => clock,
      ...extra,
    };
  }
  const PROVISION = { path: '/api/box', header: 'X-Devbox-Token' };

  test('waits through 202s, then returns the box', async () => {
    const d = deps([reply(202, { ready: false, step: 'folder', message: 'making your folder' }),
                    reply(202, { ready: false, step: 'runtime', message: 'creating your box' }), reply(200, { ready: true, box: BOX })]);
    assert.deepEqual(plain(await L.provisionBox(PROVISION, d)), BOX);
    assert.equal(d.calls.length, 3);
    assert.deepEqual(d.updates.map(u => u.message), ['making your folder', 'creating your box']);
    const { url, init } = d.calls[0];
    assert.equal(url, '/api/box');
    assert.equal(init.method, 'POST');
    assert.equal(init.credentials, 'omit');
    assert.equal(init.headers['X-Devbox-Token'], 'Bearer tok', 'the token goes in X-Devbox-Token, never in the URL');
    assert.equal(init.headers.Authorization, undefined);
  });

  test('a 403 says why there is no box, and stops', async () => {
    const d = deps([reply(403, { message: "you're not in the devbox-users group: ask an admin to add you" })]);
    await assert.rejects(L.provisionBox(PROVISION, d), /not in the devbox-users group/);
    assert.equal(d.calls.length, 1);
  });

  test('a 401 refreshes the token once, then asks again', async () => {
    const d = deps([reply(401, { message: 'Unauthorized' }), reply(200, { ready: true, box: BOX })]);
    assert.deepEqual(plain(await L.provisionBox(PROVISION, d)), BOX);
    assert.ok(d.calls.some(c => c.refresh));
    const d2 = deps([reply(401, {}), reply(401, { message: 'Unauthorized' })]);
    await assert.rejects(L.provisionBox(PROVISION, d2), /Unauthorized|401/);
  });

  test('network errors and 5xx are retried; three 500s in a row stop it', async () => {
    const d = deps([new Error('offline'), reply(503, {}), reply(200, { ready: true, box: BOX })]);
    assert.deepEqual(plain(await L.provisionBox(PROVISION, d)), BOX);
    const d2 = deps([reply(500, { message: 'x' }), reply(500, { message: 'x' }), reply(500, { message: 'setting up your box failed (AccessDeniedException)' })]);
    await assert.rejects(L.provisionBox(PROVISION, d2), /AccessDeniedException/);
  });

  test('gives up after 20 minutes of 202s', async () => {
    const replies = Array.from({ length: 1000 }, () => reply(202, { ready: false, step: 'runtime', message: 'creating your box' }));
    await assert.rejects(L.provisionBox(PROVISION, deps(replies)), /far longer/);
  });

  test('a 200 without a runtime ARN is not a box', async () => {
    const d = deps([reply(200, { ready: true, box: { name: 'x' } }), reply(200, { ready: true, box: BOX })]);
    assert.deepEqual(plain(await L.provisionBox(PROVISION, d)), BOX);
  });
});

describe('identity and session', () => {
  test('sessionId is dbx- + hex(sha256(uid:generation)) and fits AgentCore limits', async () => {
    const id = await L.sessionIdFor('00u1abcd', 1);
    assert.equal(id, `dbx-${sha('00u1abcd:1')}`);
    assert.equal(id.length, 68);
    assert.match(id, /^[a-z0-9-]+$/);
    assert.notEqual(await L.sessionIdFor('00u1abcd', 2), id, 'a reset (new generation) gives a new session');
  });

  test('the box is found by hex(sha256(uid))', async () => {
    const box = { name: 'ada', runtimeArn: 'arn:aws:bedrock-agentcore:us-east-1:1:runtime/devbox_ada-x', generation: 1 };
    const config = { boxes: { [sha('00u-ada')]: box } };
    assert.deepEqual(plain(await L.findBox(config, '00u-ada')), box);
    assert.equal(await L.findBox(config, '00u-grace'), null);
    assert.equal(await L.findBox({ boxes: {} }, 'constructor'), null, 'no prototype lookups');
    assert.equal(await L.findBox({}, '00u-ada'), null);
  });

  test('invocations URL encodes the whole ARN', () => {
    const url = L.invocationsUrl('https://bedrock-agentcore.us-east-1.amazonaws.com/', 'arn:aws:bedrock-agentcore:us-east-1:123:runtime/devbox_ada-X');
    assert.equal(url, 'https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123%3Aruntime%2Fdevbox_ada-X/invocations?qualifier=DEFAULT');
  });
});

describe('workbench configuration', () => {
  test('matches the contract', () => {
    const root = '/stable-072586267e68ece9a47aa43f8c108e0dcbf44622';
    const cfg = plain(L.workbenchConfig('d111.cloudfront.net', { serverRoot: root, webviewOrigin: 'https://d222.cloudfront.net' }));
    assert.deepEqual(cfg, {
      remoteAuthority: 'd111.cloudfront.net',
      serverBasePath: '/',
      folderUri: { scheme: 'vscode-remote', authority: 'd111.cloudfront.net', path: '/mnt/workspace/projects' },
      webviewEndpoint: `https://d222.cloudfront.net${root}/static/out/vs/workbench/contrib/webview/browser/pre`,
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
        'chat.disableAIFeatures': true,
        'workbench.secondarySideBar.defaultVisibility': 'hidden',
      },
    });
  });
});

describe('terminal mode', () => {
  test('/terminal opens the terminal; every other path is VS Code', () => {
    assert.equal(L.modeFor('/terminal'), 'terminal');
    for (const path of ['/', '/callback', '/terminal/', '/Terminal', '/terminalx', '']) assert.equal(L.modeFor(path), 'vscode', path);
  });

  test('the terminal is ready once the disk is attached and the box has set up the workspace, VS Code or not', () => {
    const ready = L.readyFor('terminal');
    const s = (volume, vscode) => ({ volume, vscode });
    assert.equal(ready(s('waiting', 'waiting')), false);
    assert.equal(ready(s('mounted', 'waiting')), false, 'the supervisor has not prepared home and projects yet');
    assert.equal(ready(s('mounted', 'starting')), true);
    assert.equal(ready(s('mounted', 'ready')), true);
    assert.equal(ready(s('mounted', 'failed')), true, 'a failing VS Code does not stop the terminal');
    assert.equal(ready(s('waiting', 'ready')), false);
    const vscode = L.readyFor('vscode');
    assert.equal(vscode(s('mounted', 'starting')), false);
    assert.equal(vscode(s('mounted', 'ready')), true);
  });

  test('a box offers the terminal unless the config says terminal: false (deploy writes true for microVM boxes)', () => {
    const arn = 'arn:aws:bedrock-agentcore:us-east-1:1:runtime/devbox_vm_ada-x';
    assert.equal(L.terminalAvailable({ name: 'ada', runtimeArn: arn, generation: 1, compute: 'microvm', terminal: true }), true);
    assert.equal(L.terminalAvailable({ name: 'ada', runtimeArn: arn, generation: 1 }), true, 'an older config without the field');
    assert.equal(L.terminalAvailable({ name: 'ada', runtimeArn: arn, generation: 1, compute: 'local', terminal: false }), false);
  });

  test('the loader loads the pinned xterm.js files', () => {
    const source = readFileSync(join(EDGE, 'web', 'devbox', 'loader.js'), 'utf8');
    assert.ok(source.includes(`'/static/${XTERM.script}'`), XTERM.script);
    assert.ok(source.includes(`'/static/${XTERM.css}'`), XTERM.css);
    assert.ok(source.includes("'/devbox/terminal.js'"));
  });
});

describe('small helpers', () => {
  test('backoff grows, is capped at 15 s and has jitter', () => {
    assert.equal(L.backoffMs(0, () => 0), 500);
    assert.equal(L.backoffMs(0, () => 1), 1000);
    assert.equal(L.backoffMs(3, () => 1), 8000);
    assert.equal(L.backoffMs(10, () => 1), 15000);
    assert.equal(L.backoffMs(10, () => 0), 7500);
  });

  test('elapsed time reads m:ss', () => {
    assert.equal(L.formatElapsed(0), '0:00');
    assert.equal(L.formatElapsed(65_400), '1:05');
    assert.equal(L.formatElapsed(8 * 60_000), '8:00');
  });
});

describe('status polling', () => {
  // script: an array of answers, or a function of the time waited (ms) that returns the next one.
  // The clock moves only by what pollStatus sleeps.
  function run(script, isReady) {
    const sleeps = [];
    const updates = [];
    let refreshes = 0;
    let t = 0;
    const promise = L.pollStatus({
      invoke: async () => {
        const next = typeof script === 'function' ? script(t) : script.shift();
        if (next instanceof Error) throw next;
        return next;
      },
      refresh: async () => { refreshes++; },
      onUpdate: u => updates.push({ ...plain(u), at: t }),
      sleep: async ms => { sleeps.push({ ms, at: t }); t += ms; },
      now: () => t,
      random: () => 1,
      pollMs: 2000,
      isReady,
    });
    return { promise, sleeps: () => sleeps.map(s => s.ms), sleepsAt: sleeps, updates, refreshes: () => refreshes, now: () => t };
  }
  const MIN = 60_000;
  const status = (vscode, extra = {}) => ({ status: 200, envelope: { v: 1, ok: true, vscode, volume: 'mounted', signedIn: false, serverStartId: 's1', ...extra } });
  const abort = () => Object.assign(new Error('aborted'), { name: 'AbortError' });

  test('waits through timeouts, 409s and a starting box until VS Code is ready', async () => {
    const r = run([abort(), { status: 409 }, { status: 429 }, { status: 503 }, status('waiting', { volume: 'waiting' }), status('starting'), status('ready')]);
    const ready = await r.promise;
    assert.equal(ready.vscode, 'ready');
    assert.deepEqual(r.sleeps(), [1000, 2000, 4000, 8000, 2000, 2000]);
    assert.deepEqual(r.updates.map(u => u.kind), ['retry', 'retry', 'retry', 'retry', 'status', 'status', 'status']);
    assert.equal(r.updates[0].reason, 'no answer within 60 s');
    assert.ok(r.updates.every(u => u.slow === false), 'a normal cold start is not slow');
  });

  test('a 401 gets one token refresh; a second one is a real refusal', async () => {
    const ok = run([{ status: 401 }, status('ready')]);
    await ok.promise;
    assert.equal(ok.refreshes(), 1);

    const refused = run([{ status: 403 }, { status: 403 }]);
    await assert.rejects(refused.promise, /turned down your sign-in/);
    assert.equal(refused.refreshes(), 1);
  });

  test('stops on errors that retrying will not fix', async () => {
    await assert.rejects(run([{ status: 404 }]).promise, /can't find this dev box/);
    await assert.rejects(run([{ status: 400 }]).promise, /HTTP 400/);
    await assert.rejects(run([{ status: 200, envelope: { v: 1, ok: false, error: 'wrong session' } }]).promise, /wrong session/);
    await assert.rejects(run([{ status: 200, envelope: null }]).promise, /unexpected reply/);
  });

  test('backoff resets after a good answer', async () => {
    const r = run([{ status: 409 }, { status: 409 }, status('starting'), { status: 409 }, status('ready')]);
    await r.promise;
    assert.deepEqual(r.sleeps(), [1000, 2000, 2000, 1000]);
  });

  test('past 10 minutes it says the wait is too long and polls once a minute, and a 409 keeps it going', async () => {
    const r = run(t => (t < 30 * MIN ? { status: 409 } : status('ready')));
    assert.equal((await r.promise).vscode, 'ready', 'a slow AgentCore is not given up on');
    const before = r.updates.filter(u => u.at < 10 * MIN);
    const after = r.updates.filter(u => u.at >= 10 * MIN);
    assert.ok(before.length > 5 && before.every(u => !u.slow), 'not slow before 10 minutes');
    assert.ok(after.length > 5 && after.every(u => u.slow), 'slow from 10 minutes on');
    const late = r.sleepsAt.filter(s => s.at >= 10 * MIN).map(s => s.ms);
    assert.ok(late.length > 0 && late.every(ms => ms === MIN), `one poll a minute once slow (got ${late.join(', ')})`);
    assert.ok(r.sleepsAt.filter(s => s.at < 10 * MIN).every(s => s.ms <= 15_000), 'normal backoff before');
  });

  test('past 20 minutes a 424 ends the wait with a pointer to devbox.py status', async () => {
    const r = run(() => ({ status: 424 }));
    await assert.rejects(r.promise, err => {
      assert.match(err.message, /HTTP 424/);
      assert.match(err.message, /devbox\.py status/);
      assert.match(err.message, /^After 20 minutes/);
      return true;
    });
    assert.ok(r.now() >= 20 * MIN && r.now() < 22 * MIN, `gave up at ${r.now() / MIN} min`);
    assert.ok(r.updates.some(u => u.kind === 'retry' && u.reason === 'AgentCore said 424' && !u.slow), 'an early 424 is retried');
  });

  test('past 20 minutes a disk that never attaches, or VS Code that keeps failing, ends the wait', async () => {
    const disk = run(() => status('waiting', { volume: 'waiting' }));
    await assert.rejects(disk.promise, /waited 20 minutes for its disk\. Ask your admin to run devbox\.py status/);
    assert.ok(disk.now() >= 20 * MIN && disk.now() < 22 * MIN);

    const failing = run(() => status('failed'));
    await assert.rejects(failing.promise, /keeps failing to start/);

    const early = run(t => (t < 5 * MIN ? status('failed') : status('ready')));
    assert.equal((await early.promise).vscode, 'ready', 'a VS Code restart during a normal start is waited out');
  });

  test('the terminal stops waiting as soon as the box has set up, and a failing VS Code does not end its wait', async () => {
    const r = run([status('waiting', { volume: 'waiting' }), status('waiting'), status('starting')], L.readyFor('terminal'));
    assert.equal((await r.promise).vscode, 'starting');
    assert.equal(r.updates.length, 3);

    const failing = run(() => status('failed'), L.readyFor('terminal'));
    assert.equal((await failing.promise).vscode, 'failed', 'ready for the terminal at once');

    const disk = run(() => status('waiting', { volume: 'waiting' }), L.readyFor('terminal'));
    await assert.rejects(disk.promise, /waited 20 minutes for its disk/, 'no disk still ends the wait');
  });

  test('past 20 minutes a box that is still starting (disk attached) is waited for', async () => {
    const r = run(t => (t < 25 * MIN ? status('starting') : status('ready')));
    assert.equal((await r.promise).vscode, 'ready');
  });
});

// Just enough IndexedDB for the owner check: open (with upgrade), one object store, get/put,
// databases() and deleteDatabase().
function fakeIndexedDB(initial) {
  const dbs = new Map(initial.map(name => [name, new Map()]));
  const deleted = [];
  const later = fn => setTimeout(fn, 0);
  const request = run => {
    const req = {};
    later(() => {
      try {
        req.result = run(req);
        req.onsuccess?.();
      } catch (err) {
        req.error = err;
        req.onerror?.();
      }
    });
    return req;
  };
  const idb = {
    deleted,
    names: () => [...dbs.keys()],
    open(name) {
      return request(req => {
        const isNew = !dbs.has(name);
        if (isNew) dbs.set(name, new Map());
        const stores = dbs.get(name);
        const db = {
          createObjectStore: store => stores.set(store, new Map()),
          transaction: store => ({
            objectStore: () => ({
              get: key => request(() => stores.get(store).get(key)),
              put: (value, key) => request(() => { stores.get(store).set(key, value); return key; }),
            }),
          }),
          close() {},
        };
        req.result = db;
        if (isNew) req.onupgradeneeded?.();
        return db;
      });
    },
    databases: async () => [...dbs.keys()].map(name => ({ name, version: 1 })),
    deleteDatabase(name) {
      return request(() => {
        dbs.delete(name);
        deleted.push(name);
      });
    },
  };
  return idb;
}

describe('browser state per person', () => {
  test('deletes VS Code state left by someone else, and only that', async () => {
    const idb = fakeIndexedDB(['vscode-web-db', 'vscode-web-state-db-global', 'vscode-web-state-db-1a2b', 'something-else']);
    assert.equal(await L.resetVscodeStateIfOwnerChanged({ indexedDB: idb, ownerKey: 'owner-ada' }), true);
    assert.deepEqual(idb.deleted.sort(), ['vscode-web-db', 'vscode-web-state-db-1a2b', 'vscode-web-state-db-global']);
    assert.ok(idb.names().includes('something-else'));

    idb.deleted.length = 0;
    assert.equal(await L.resetVscodeStateIfOwnerChanged({ indexedDB: idb, ownerKey: 'owner-ada' }), false, 'same person: keep');
    assert.deepEqual(idb.deleted, []);

    assert.equal(await L.resetVscodeStateIfOwnerChanged({ indexedDB: idb, ownerKey: 'owner-grace' }), true, 'someone else: reset');
  });

  test('without databases(), the first person goes ahead and the next one is stopped (no partial wipe)', async () => {
    const idb = fakeIndexedDB(['vscode-web-db']);
    delete idb.databases;
    assert.equal(await L.resetVscodeStateIfOwnerChanged({ indexedDB: idb, ownerKey: 'owner-ada' }), true, 'nobody before: go ahead');
    assert.deepEqual(idb.deleted.sort(), ['vscode-web-db', 'vscode-web-state-db-global']);
    assert.equal(await L.resetVscodeStateIfOwnerChanged({ indexedDB: idb, ownerKey: 'owner-ada' }), false, 'same person: keep');

    for (const name of ['vscode-web-db', 'vscode-web-state-db-global', 'vscode-web-state-db-1a2b']) idb.open(name);
    await new Promise(r => setTimeout(r, 5));
    idb.deleted.length = 0;
    await assert.rejects(
      L.resetVscodeStateIfOwnerChanged({ indexedDB: idb, ownerKey: 'owner-grace' }),
      /can't clear the VS Code state of the last person.*current browser, or your own browser profile/,
    );
    assert.deepEqual(idb.deleted, [], 'nothing deleted');
    assert.ok(idb.names().includes('vscode-web-state-db-1a2b'), "ada's state is still there, and VS Code doesn't start for grace");
    await assert.rejects(L.resetVscodeStateIfOwnerChanged({ indexedDB: idb, ownerKey: 'owner-grace' }), /can't clear/, 'the owner record still says ada');
    assert.equal(await L.resetVscodeStateIfOwnerChanged({ indexedDB: idb, ownerKey: 'owner-ada' }), false, 'ada can still use it');
  });
});

describe('AWS sign-in hint after VS Code opens', () => {
  function fakeView() {
    const shown = [];
    return {
      shown,
      banner(text, actions, opts) {
        const entry = { text, actions, opts, removed: false };
        shown.push(entry);
        return { bar: { remove: () => { entry.removed = true; } }, label: {} };
      },
    };
  }
  const visible = view => view.shown.filter(b => !b.removed);

  test('shows while not signed in, where to find the device code, and goes when signed in', () => {
    const view = fakeView();
    const hint = L.awsSignInHint(view);
    hint.update(false);
    hint.update(false);
    assert.equal(visible(view).length, 1, 'one banner, not one per poll');
    const [b] = visible(view);
    assert.match(b.text, /Claude Code shows a sign-in URL and a code in its terminal/);
    assert.match(b.text, /Terminal › New Terminal/);
    assert.equal(b.opts.code, 'aws sso login --sso-session devbox --use-device-code --no-browser');
    assert.equal(b.opts.id, 'devbox-aws-hint', 'its own banner, so the restart banner does not replace it');
    assert.equal(b.opts.role, 'status');
    hint.update(true);
    assert.equal(visible(view).length, 0);
  });

  test('Dismiss hides it until the person has signed in and dropped out again', () => {
    const view = fakeView();
    const hint = L.awsSignInHint(view);
    hint.update(false);
    visible(view)[0].actions.find(a => a.label === 'Dismiss').run();
    hint.update(false);
    assert.equal(visible(view).length, 0, 'stays dismissed');
    hint.update(true);
    hint.update(false);
    assert.equal(visible(view).length, 1, 'back after the sign-in ended');
  });

  test('in the terminal it points at this terminal, without the VS Code shell command', () => {
    const view = fakeView();
    const hint = L.awsSignInHint(view, 'terminal');
    hint.update(false);
    const [b] = visible(view);
    assert.match(b.text, /Claude Code shows a sign-in URL and a code in this terminal; open the URL and enter the code\./);
    assert.doesNotMatch(b.text, /New Terminal/);
    assert.equal(b.opts.code, undefined);
    assert.equal(b.opts.id, 'devbox-aws-hint');
    hint.update(true);
    assert.equal(visible(view).length, 0);
  });

  test('nothing when already signed in', () => {
    const view = fakeView();
    L.awsSignInHint(view).update(true);
    assert.equal(view.shown.length, 0);
  });
});
