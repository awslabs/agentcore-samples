// Identifiers derived exactly as deploy and the loader derive them.
import crypto from 'node:crypto';

const sha256hex = (s) => crypto.createHash('sha256').update(s, 'utf8').digest('hex');

// sessionId = "dbx-" + hex(sha256(uid + ":" + generation)): 68 chars of [a-z0-9-].
export function sessionIdFor(uid, generation) {
  return `dbx-${sha256hex(`${uid}:${generation}`)}`;
}

// The key of a person's entry in devbox-config.json "boxes".
export function boxKeyFor(uid) {
  return sha256hex(uid);
}

export function base64url(input) {
  return Buffer.from(input).toString('base64url');
}

// The WebSocket subprotocols a browser offers AgentCore.
export function bearerSubprotocols(token) {
  return [`base64UrlBearerAuthorization.${base64url(token)}`, 'base64UrlBearerAuthorization'];
}
