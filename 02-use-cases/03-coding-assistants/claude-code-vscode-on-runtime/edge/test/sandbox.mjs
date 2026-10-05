// Runs our browser scripts (classic scripts, not modules) in a vm context with just the web APIs
// each test provides, so the real files are tested rather than copies.

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import vm from 'node:vm';

import { EDGE } from './fixture.mjs';

const WEB_APIS = {
  URL, URLSearchParams, TextEncoder, TextDecoder, Blob, DOMException, EventTarget, Event,
  Headers, Request, Response, AbortController, atob, btoa, console, crypto: globalThis.crypto,
};

export function makeSandbox(globals = {}) {
  const sandbox = { ...WEB_APIS, ...globals };
  sandbox.window = sandbox;
  sandbox.self ??= sandbox;
  return vm.createContext(sandbox);
}

export function runScript(context, rel) {
  const path = join(EDGE, 'web', ...rel.split('/'));
  vm.runInContext(readFileSync(path, 'utf8'), context, { filename: path });
  return context;
}

// A controllable clock and timer queue.
export function fakeTime(start = 1_000_000) {
  let now = start;
  let seq = 0;
  const timers = new Map();
  const clock = {
    now: () => now,
    setTimeout: (fn, ms = 0) => {
      const id = ++seq;
      timers.set(id, { at: now + Math.max(0, ms), fn });
      return id;
    },
    clearTimeout: id => { timers.delete(id); },
    pending: () => timers.size,
    // Runs every timer due by now + ms, in time order, including timers they schedule.
    advance(ms) {
      const until = now + ms;
      for (;;) {
        let next = null;
        for (const [id, t] of timers) if (t.at <= until && (!next || t.at < next[1].at)) next = [id, t];
        if (!next) break;
        timers.delete(next[0]);
        now = next[1].at;
        next[1].fn();
      }
      now = until;
    },
  };
  clock.Date = { now: clock.now };
  return clock;
}
