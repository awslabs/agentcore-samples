"""Open the authorization URL for an implicit-sync target.

This is only needed for Method 1 (implicit sync) targets, and only as a
fallback: it opens the browser but does NOT complete session binding, so you
are left to call CompleteResourceTokenAuth yourself with the session URI that
comes back on the redirect.

Prefer callback_server.py, which listens on localhost:8080, receives the
redirect, and calls CompleteResourceTokenAuth for you:

    uv run python scripts/callback_server.py --user-id "<user-id>" --auth-url "<url>"

Usage:
    uv run python scripts/complete_auth.py <authorization-url> <user-id>
"""

import sys
import webbrowser


def main():
    if len(sys.argv) < 3:
        print("Usage: uv run python scripts/complete_auth.py <auth-url> <user-id>")
        print("\nGet these values from the deploy_target_implicit.py output.")
        sys.exit(1)

    auth_url = sys.argv[1]
    user_id = sys.argv[2]

    print(
        f"Authorization URL: {auth_url}"
    )  # lgtm[py/clear-text-logging-sensitive-data]
    print(f"User ID: {user_id}")
    webbrowser.open(auth_url)

    print("\nComplete the authorization in your browser.")
    print("The redirect carries a session_id query parameter -- pass it to")
    print("CompleteResourceTokenAuth along with the user id above:")
    print(
        "\n  aws bedrock-agentcore complete-resource-token-auth \\\n"
        f'    --user-identifier \'{{"userId":"{user_id}"}}\' \\\n'
        '    --session-uri "<session_id from the redirect>"'
    )


if __name__ == "__main__":
    main()
