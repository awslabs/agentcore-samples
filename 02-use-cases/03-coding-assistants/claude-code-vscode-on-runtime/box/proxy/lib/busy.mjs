// /ping status. AgentCore keeps a session alive while it reports HealthyBusy, and its idle timer
// only runs from the last status change, so time_of_last_update moves only when the status does
// (a timestamp that advances on every ping would stop the idle timeout from ever firing).
//
// Two things keep the box busy: an open /ws, and Claude working on a turn (the supervisor's
// agentBusy, from the managed agent-busy hook), so a turn keeps running after the laptop closes.

export const BUSY_TAIL_MS = 120_000;

export class BusyTracker {
  #now;
  #open = 0;
  #agent = false;
  #status = 'Healthy';
  #since;
  #tailUntil = 0;

  constructor(now = Date.now) {
    this.#now = now;
    this.#since = now();
  }

  get open() {
    return this.#open;
  }

  get agentBusy() {
    return this.#agent;
  }

  opened() {
    this.#settle();
    this.#open += 1;
    this.#markBusy();
  }

  closed() {
    this.#settle();
    if (this.#open === 0) return;
    this.#open -= 1;
    // A browser reload or a WebSocket reconnect shouldn't flap the status.
    if (this.#open === 0) this.#tailUntil = this.#now() + BUSY_TAIL_MS;
  }

  // Called with the supervisor's latest agentBusy before each answer. The same tail follows the
  // end of a turn as follows the last socket.
  agent(busy) {
    this.#settle();
    if (busy === this.#agent) return;
    this.#agent = busy;
    if (busy) this.#markBusy();
    else if (this.#open === 0) this.#tailUntil = this.#now() + BUSY_TAIL_MS;
  }

  ping() {
    this.#settle();
    return { status: this.#status, time_of_last_update: Math.floor(this.#since / 1000) };
  }

  #markBusy() {
    if (this.#status !== 'HealthyBusy') {
      this.#status = 'HealthyBusy';
      this.#since = this.#now();
    }
  }

  // The switch back to Healthy happens when the tail ends, not when someone next asks.
  #settle() {
    if (this.#status === 'HealthyBusy' && this.#open === 0 && !this.#agent && this.#now() >= this.#tailUntil) {
      this.#status = 'Healthy';
      this.#since = this.#tailUntil;
    }
  }
}
