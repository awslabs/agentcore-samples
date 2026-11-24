#!/usr/bin/env python3
"""One bounded IAM Runtime invocation; output is not a persistence assertion.

CLI equivalent: agentcore invoke --runtime claimsagent --target dev --payload '{"prompt":"..."}'
"""

import argparse

from cloud_checks import context
from test_e2e import invoke


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--prompt", required=True)
    args = parser.parse_args()
    if not args.allow_cloud:
        parser.error("Explicit --allow-cloud required; this calls models")
    _, session, outputs = context()
    print(invoke(session, outputs["RuntimeArn"], args.prompt))


if __name__ == "__main__":
    main()
