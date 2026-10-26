// Box -> browser direction of the WebSocket relay. AgentCore closes a connection whose frames are
// bigger than 32 KB (1009) or that sends more than 250 frames a second (1008), while VS Code writes
// messages of up to 256 KB. VS Code reads the stream as bytes, so splitting a message is safe.

export const MAX_CHUNK = 32000;
export const MAX_PER_SECOND = 200;

export function* chunks(buffer, size = MAX_CHUNK) {
  if (buffer.length === 0) {
    yield buffer;
    return;
  }
  for (let i = 0; i < buffer.length; i += size) yield buffer.subarray(i, i + size);
}

// Sends at most `perSecond` messages in any 1-second window (a sliding window, so the limit holds
// however the far end measures it).
export class Pacer {
  #send;
  #perSecond;
  #now;
  #setTimer;
  #clearTimer;
  #queue = [];
  #bytes = 0;
  #sentAt = [];
  #timer = null;
  #stopped = false;

  constructor(send, { perSecond = MAX_PER_SECOND, chunkSize = MAX_CHUNK, now = Date.now,
    setTimer = setTimeout, clearTimer = clearTimeout, onProgress = () => {} } = {}) {
    this.#send = send;
    this.#perSecond = perSecond;
    this.chunkSize = chunkSize;
    this.#now = now;
    this.#setTimer = setTimer;
    this.#clearTimer = clearTimer;
    this.onProgress = onProgress;
  }

  get pendingBytes() {
    return this.#bytes;
  }

  get pendingMessages() {
    return this.#queue.length;
  }

  push(buffer) {
    if (this.#stopped) return;
    for (const piece of chunks(buffer, this.chunkSize)) {
      this.#queue.push(piece);
      this.#bytes += piece.length;
    }
    this.#pump();
  }

  stop() {
    this.#stopped = true;
    if (this.#timer) this.#clearTimer(this.#timer);
    this.#timer = null;
    this.#queue = [];
    this.#bytes = 0;
  }

  #pump() {
    if (this.#timer || this.#stopped) return;
    while (this.#queue.length > 0) {
      const now = this.#now();
      while (this.#sentAt.length > 0 && now - this.#sentAt[0] >= 1000) this.#sentAt.shift();
      if (this.#sentAt.length >= this.#perSecond) {
        const wait = Math.max(1, 1000 - (now - this.#sentAt[0]));
        this.#timer = this.#setTimer(() => {
          this.#timer = null;
          this.#pump();
        }, wait);
        this.onProgress();
        return;
      }
      const piece = this.#queue.shift();
      this.#bytes -= piece.length;
      this.#sentAt.push(now);
      this.#send(piece);
      if (this.#stopped) return;
    }
    this.onProgress();
  }
}
