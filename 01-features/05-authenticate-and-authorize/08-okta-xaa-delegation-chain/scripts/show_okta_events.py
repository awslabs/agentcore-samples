"""Show Okta's side of the exchanges, from the System Log.

    .venv/bin/python scripts/show_okta_events.py
    .venv/bin/python scripts/show_okta_events.py --since 2h

AWS logs show what the interceptor asked for; this shows what Okta decided. Useful when
a leg fails, because Okta's `outcome.reason` names the cause far more precisely than the
OAuth error returned to the client.

Read it alongside `scripts/show_trace.py`: the actor rotating from the user to the AI
Agent across consecutive grant events IS the delegation chain.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _common import OktaAdmin, load_env, must_env, okta_org_url

# Okta emits GRANULAR event types, not one generic grant event. These are the ones that
# make up this sample's chain -- discovered by dumping the System Log rather than guessed:
#
#   app.oauth2.as.authorize.code        hop A: the authorization code
#   app.oauth2.as.token.grant.id_token  hop A: T_id issued
#   app.oauth2.as.token.grant.access_token  hop A: T_user, and leg 2: T_tool
#   app.oauth2.token.grant.id_jag       ID-JAG LEG 1, at the org server
#   app.oauth2.token.grant              the FAILURE variant (no .suffix)
PREFIXES = ("app.oauth2.",)
ALSO = ("user.authentication.sso", "policy.evaluate_sign_on")

HOP = {
    "app.oauth2.as.authorize.code": "hop A · authorization code",
    "app.oauth2.as.token.grant.id_token": "hop A · T_id issued",
    "app.oauth2.as.token.grant.access_token": "hop A/C/leg 2 · access token issued",
    "app.oauth2.as.token.grant.refresh_token": "hop A · refresh token issued",
    "app.oauth2.token.grant.id_jag": "★ ID-JAG LEG 1 · at the ORG server",
    "app.oauth2.token.grant": "token grant (failure variant)",
    "app.oauth2.token.grant.access_token": "token grant · access token",
    "user.authentication.sso": "user signed in",
    "policy.evaluate_sign_on": "sign-on policy evaluated",
}


def interesting(event_type: str) -> bool:
    return event_type in ALSO or any(event_type.startswith(p) for p in PREFIXES)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="1h", help="Look-back window, e.g. 30m, 2h (default: %(default)s).")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--all", action="store_true", help="Do not filter to token/auth events.")
    args = ap.parse_args()
    load_env()
    okta = OktaAdmin(okta_org_url(), must_env("OKTA_API_TOKEN"))

    unit = args.since[-1]
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit)
    if not mult or not args.since[:-1].isdigit():
        sys.exit("--since must look like 90s, 30m, 2h or 1d")
    start = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=int(args.since[:-1]) * mult)
    # Okta's System Log returns ASCENDING by default, so without sortOrder you get the
    # OLDEST events in the window -- the opposite of what you want when debugging the
    # call you just made.
    query = urllib.parse.urlencode(
        {
            "since": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 200,
            "sortOrder": "DESCENDING",
        }
    )

    events = okta.get(f"/logs?{query}")
    if not isinstance(events, list):
        sys.exit(f"unexpected response: {str(events)[:200]}")

    rows = [e for e in events if args.all or interesting(e.get("eventType") or "")]
    if not rows:
        print(f"No matching Okta events in the last {args.since}.")
        print("  Sign in or make a tool call first, then re-run.")
        return

    print(f"{len(rows)} event(s), newest first:\n")
    for e in rows[: args.limit]:
        when = (e.get("published") or "")[11:19]
        outcome = (e.get("outcome") or {}).get("result")
        reason = (e.get("outcome") or {}).get("reason") or ""
        actor = (e.get("actor") or {}).get("displayName") or (e.get("actor") or {}).get("alternateId")
        # The client that presented credentials -- this is the value that rotates from
        # the sign-in app to the AI Agent as the chain progresses.
        client = (e.get("client") or {}).get("id") or ""
        targets = ", ".join(
            f"{t.get('type')}:{t.get('displayName') or t.get('alternateId')}" for t in (e.get("target") or [])[:3]
        )
        flag = "✓" if outcome == "SUCCESS" else "✗"
        etype = e.get("eventType") or ""
        label = HOP.get(etype, "")
        print(f"  {flag} {when}  {etype}")
        if label:
            print(f"      {label}")
        print(f"      actor={actor}  client={client}")
        if targets:
            print(f"      target={targets}")
        if outcome != "SUCCESS" or reason:
            print(f"      outcome={outcome} reason={reason}")
        print()

    print("  Reading this, newest first — a healthy request looks like:")
    print("    app.oauth2.as.authorize.code              hop A begins")
    print("    app.oauth2.as.token.grant.id_token        T_id")
    print("    app.oauth2.as.token.grant.access_token    T_user")
    print("    app.oauth2.token.grant.id_jag             ID-JAG leg 1 at the ORG server")
    print("    app.oauth2.as.token.grant.access_token    T_tool from leg 2 at the resource AS")
    print()
    print("    ✗ invalid_subject_token_no_delegation_link  leg 1 got the wrong token, or the")
    print("                                                User access binding is missing")
    print("    ✗ a reason naming a policy                  that leg is unauthorised")


if __name__ == "__main__":
    main()
