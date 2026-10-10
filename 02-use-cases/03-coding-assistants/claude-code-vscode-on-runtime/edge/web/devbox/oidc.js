// Dev box sign-in: OpenID Connect authorization code flow with PKCE (S256) against Okta, by hand.
//
// Tokens live only in this closure, in the memory of the one page. The PKCE verifier, state and nonce
// must survive the redirect to Okta and back, so they sit in sessionStorage for that round trip only
// and are deleted on return (they are single-use and not credentials on their own). The code comes
// back in the URL fragment (response_mode=fragment), which never reaches CloudFront or the Lambda.
(function (root) {
  'use strict';

  const TXN_KEY = 'devbox-oidc-txn';
  const SIGNED_OUT_KEY = 'devbox-signed-out';
  const REFRESH_AT = 0.75;
  const REFRESH_RETRY_MS = 30000;
  // Okta's answers to prompt=none that mean "the user has to interact".
  const INTERACTION_ERRORS = ['login_required', 'interaction_required', 'consent_required', 'account_selection_required'];
  // Okta always sends the browser back to the one registered redirect URI (/callback). The page then puts
  // the path it was signed in from back in the address bar: one lowercase segment such as / or /terminal.
  const RETURN_PATH = /^\/[a-z0-9-]{0,32}$/;

  function returnPathOf(value) {
    return typeof value === 'string' && RETURN_PATH.test(value) ? value : '/';
  }

  function base64Url(bytes) {
    let binary = '';
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return root.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function base64UrlDecode(text) {
    const b64 = text.replace(/-/g, '+').replace(/_/g, '/');
    const binary = root.atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  // The payload of a JWT, without any validation: the page only needs claims such as uid. AgentCore
  // validates the token itself.
  function decodeJwtPayload(jwt) {
    const parts = String(jwt || '').split('.');
    if (parts.length !== 3) throw new Error('not a JWT');
    return JSON.parse(new TextDecoder().decode(base64UrlDecode(parts[1])));
  }

  function randomString(crypto, bytes) {
    return base64Url(crypto.getRandomValues(new Uint8Array(bytes)));
  }

  async function pkceChallenge(crypto, verifier) {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
    return base64Url(new Uint8Array(digest));
  }

  function create(options) {
    const issuer = String(options.issuer).replace(/\/+$/, '');
    const clientId = options.clientId;
    const scopes = options.scopes;
    const redirectUri = options.redirectUri;
    const postLogoutRedirectUri = options.postLogoutRedirectUri;
    const env = {
      fetch: options.fetch || root.fetch.bind(root),
      crypto: options.crypto || root.crypto,
      location: options.location || root.location,
      history: options.history || root.history,
      storage: options.storage || root.sessionStorage,
      document: options.document || root.document,
      now: options.now || Date.now,
      setTimeout: options.setTimeout || root.setTimeout.bind(root),
      clearTimeout: options.clearTimeout || root.clearTimeout.bind(root),
    };
    const onSessionEnded = options.onSessionEnded || function () {};

    let tokens = null;
    let refreshTimer = null;
    let refreshing = null;
    const listeners = [];

    async function signIn(opts) {
      const promptNone = Boolean(opts && opts.promptNone);
      const then = (opts && opts.then) || null;
      const returnTo = returnPathOf(opts && opts.returnTo);
      const verifier = randomString(env.crypto, 32);
      const state = randomString(env.crypto, 16);
      const nonce = randomString(env.crypto, 16);
      env.storage.setItem(TXN_KEY, JSON.stringify({ verifier: verifier, state: state, nonce: nonce, promptNone: promptNone, then: then, returnTo: returnTo }));
      const params = new URLSearchParams({
        client_id: clientId,
        response_type: 'code',
        response_mode: 'fragment',
        scope: scopes,
        redirect_uri: redirectUri,
        state: state,
        nonce: nonce,
        code_challenge: await pkceChallenge(env.crypto, verifier),
        code_challenge_method: 'S256',
      });
      if (promptNone) params.set('prompt', 'none');
      env.location.assign(issuer + '/v1/authorize?' + params.toString());
    }

    async function tokenRequest(form) {
      const res = await env.fetch(issuer + '/v1/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded', Accept: 'application/json' },
        body: form.toString(),
        credentials: 'omit',
        cache: 'no-store',
      });
      let data = {};
      try {
        data = await res.json();
      } catch (e) {
        data = {};
      }
      if (!res.ok) {
        const err = new Error(data.error_description || data.error || 'token endpoint answered ' + res.status);
        err.code = data.error || 'http_' + res.status;
        err.status = res.status;
        throw err;
      }
      if (!data.access_token || !(Number(data.expires_in) > 0)) throw new Error('the token endpoint sent no access token');
      return data;
    }

    function accept(data) {
      const issuedAt = env.now();
      tokens = {
        accessToken: data.access_token,
        idToken: data.id_token || (tokens && tokens.idToken) || null,
        refreshToken: data.refresh_token || (tokens && tokens.refreshToken) || null,
        issuedAt: issuedAt,
        expiresAt: issuedAt + Number(data.expires_in) * 1000,
      };
      scheduleRefresh((tokens.expiresAt - issuedAt) * REFRESH_AT);
      for (const fn of listeners) {
        try {
          fn();
        } catch (e) {
          // a listener's failure must not break sign-in
        }
      }
    }

    function scheduleRefresh(delayMs) {
      env.clearTimeout(refreshTimer);
      refreshTimer = null;
      if (!tokens || !tokens.refreshToken) return;
      refreshTimer = env.setTimeout(function () {
        refresh().catch(function () {});
      }, Math.max(0, delayMs));
    }

    // Returns null when this page load is not an Okta redirect, otherwise the outcome (with returnTo, the
    // path now in the address bar).
    async function handleRedirect() {
      const onCallback = env.location.pathname === new URL(redirectUri).pathname;
      const params = new URLSearchParams(String(env.location.hash || '').replace(/^#/, ''));
      if (!onCallback || (!params.has('code') && !params.has('error'))) return null;
      const raw = env.storage.getItem(TXN_KEY);
      env.storage.removeItem(TXN_KEY);
      let txn = null;
      try {
        txn = raw ? JSON.parse(raw) : null;
      } catch (e) {
        txn = null;
      }
      const returnTo = returnPathOf(txn && txn.returnTo);
      // Drop code and state from the address bar and the history entry, and go back to where sign-in started.
      env.history.replaceState(null, '', returnTo);
      if (!txn) return { error: 'no_transaction', description: 'This sign-in was not started from this tab.', returnTo: returnTo };
      if (params.get('state') !== txn.state) {
        return { error: 'state_mismatch', description: 'The sign-in response did not match the request.', returnTo: returnTo };
      }
      if (params.has('error')) {
        const error = params.get('error');
        return {
          error: error,
          description: params.get('error_description') || '',
          needsInteraction: txn.promptNone && INTERACTION_ERRORS.indexOf(error) >= 0,
          then: txn.then,
          returnTo: returnTo,
        };
      }
      const data = await tokenRequest(new URLSearchParams({
        grant_type: 'authorization_code',
        client_id: clientId,
        code: params.get('code'),
        redirect_uri: redirectUri,
        code_verifier: txn.verifier,
      }));
      if (data.id_token && decodeJwtPayload(data.id_token).nonce !== txn.nonce) {
        return { error: 'nonce_mismatch', description: 'The ID token does not belong to this sign-in.', returnTo: returnTo };
      }
      accept(data);
      return { ok: true, then: txn.then, returnTo: returnTo };
    }

    // Refresh-token grant with rotation. Concurrent callers share one request.
    function refresh() {
      if (refreshing) return refreshing;
      if (!tokens || !tokens.refreshToken) return Promise.reject(new Error('no refresh token'));
      const refreshToken = tokens.refreshToken;
      refreshing = tokenRequest(new URLSearchParams({ grant_type: 'refresh_token', client_id: clientId, refresh_token: refreshToken }))
        .then(accept, function (err) {
          if (err.status === 400 || err.status === 401) {
            // invalid_grant and friends: the Okta session or the refresh token is over.
            if (tokens) tokens.refreshToken = null;
            onSessionEnded(err);
          } else if (tokens && tokens.expiresAt > env.now()) {
            scheduleRefresh(REFRESH_RETRY_MS);
          }
          throw err;
        })
        .finally(function () {
          refreshing = null;
        });
      return refreshing;
    }

    // Refreshes now if the access token expires within minValidityMs (timers can be throttled in
    // background tabs, so the page also calls this when it becomes visible).
    async function ensureFresh(minValidityMs) {
      if (tokens && tokens.refreshToken && tokens.expiresAt - env.now() < minValidityMs) await refresh();
    }

    async function signOut() {
      const current = tokens;
      tokens = null;
      env.clearTimeout(refreshTimer);
      refreshTimer = null;
      if (current && current.refreshToken) {
        try {
          await env.fetch(issuer + '/v1/revoke', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: new URLSearchParams({ token: current.refreshToken, token_type_hint: 'refresh_token', client_id: clientId }).toString(),
            credentials: 'omit',
            cache: 'no-store',
          });
        } catch (e) {
          // Still end the Okta session below.
        }
      }
      env.storage.setItem(SIGNED_OUT_KEY, '1');
      if (!current || !current.idToken) {
        env.location.assign(postLogoutRedirectUri);
        return;
      }
      // RP-initiated logout as a form POST, so the ID token never appears in a URL.
      const doc = env.document;
      const form = doc.createElement('form');
      form.method = 'POST';
      form.action = issuer + '/v1/logout';
      const fields = { id_token_hint: current.idToken, post_logout_redirect_uri: postLogoutRedirectUri };
      for (const name of Object.keys(fields)) {
        const input = doc.createElement('input');
        input.type = 'hidden';
        input.name = name;
        input.value = fields[name];
        form.appendChild(input);
      }
      doc.body.appendChild(form);
      form.submit();
    }

    function consumeSignedOut() {
      const flagged = env.storage.getItem(SIGNED_OUT_KEY) === '1';
      env.storage.removeItem(SIGNED_OUT_KEY);
      return flagged;
    }

    return {
      signIn: signIn,
      handleRedirect: handleRedirect,
      refresh: refresh,
      ensureFresh: ensureFresh,
      signOut: signOut,
      consumeSignedOut: consumeSignedOut,
      getAccessToken: function () {
        return tokens ? tokens.accessToken : '';
      },
      accessTokenClaims: function () {
        return tokens ? decodeJwtPayload(tokens.accessToken) : null;
      },
      expiresAt: function () {
        return tokens ? tokens.expiresAt : 0;
      },
      onTokens: function (fn) {
        listeners.push(fn);
      },
    };
  }

  root.DevboxOidc = Object.freeze({
    create: create,
    base64Url: base64Url,
    base64UrlDecode: base64UrlDecode,
    decodeJwtPayload: decodeJwtPayload,
    pkceChallenge: pkceChallenge,
    randomString: randomString,
  });
})(typeof window !== 'undefined' ? window : globalThis);
