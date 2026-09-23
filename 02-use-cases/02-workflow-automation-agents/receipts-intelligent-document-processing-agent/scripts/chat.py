#!/usr/bin/env python3
"""Interactive chat with the receipts agent about a user's expenses (the demo REPL).

  python3 scripts/chat.py --user user-001
  you> how much did I spend at Mr D.I.Y.?
  agent> ...
  you> and my most recent receipt?
  agent> ...

Mints ONE KMS-HMAC identity token for the session (bound to --user) and reuses it for
each question. The agent derives user_id only from that verified token — the IDOR fix.
Type 'exit' / 'quit' / Ctrl-D to leave.
"""

import argparse

from ask import ask  # reuse the one-shot path (token mint + invoke)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="the user_id to authenticate AS")
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--stack", default="AgentCore-ReceiptsAgent-dev")
    args = ap.parse_args()

    print(f"Chatting as {args.user}. Ask about your expenses. (exit/quit to leave)\n")
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit"):
            break
        print("agent> " + ask(q, args.user, args.region, args.stack) + "\n")


if __name__ == "__main__":
    main()
