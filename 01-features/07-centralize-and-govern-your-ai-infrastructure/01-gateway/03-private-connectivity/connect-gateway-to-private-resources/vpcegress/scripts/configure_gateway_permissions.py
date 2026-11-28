"""Write local CDK grants for SDK-owned providers, without any AWS calls.

Run after create_credential.py, then `agentcore deploy --yes --skip-bootstrap`
before creating its target. Repeat all desired --credential arguments; this
writes the complete desired grant set, not an ever-growing union of privileges.
"""

import argparse
import re

from lifecycle import STATE_DIR, load, record_path, save


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="vpc-egress-gateway")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Remove generated grants after all SDK targets/providers are gone",
    )
    parser.add_argument("--credential", action="append", default=[])
    parser.add_argument("--lambda-arn", action="append", default=[])
    args = parser.parse_args(argv)
    if args.clear:
        if args.credential or args.lambda_arn:
            parser.error("--clear cannot be combined with grants")
        for path in STATE_DIR.glob("target-*.json"):
            if load(path).get("status") != "DELETED":
                parser.error(f"Confirm target absence first: {path}")
        (STATE_DIR / "gateway-permissions.json").unlink(missing_ok=True)
        return
    providers = []
    for name in args.credential:
        record = load(record_path("credential", name))
        if record.get("status") != "CREATED" or not record.get("secretArn"):
            parser.error(f"Credential {name} is not created or lacks its secret ARN")
        providers.append(
            {
                "type": record["type"],
                "providerArn": record["arn"],
                "secretArn": record["secretArn"],
            }
        )
    if len(set(args.credential)) != len(args.credential):
        parser.error("Duplicate credential")
    if any(
        not re.fullmatch(
            r"arn:[^:]+:lambda:[^:]+:\d{12}:function:[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+)?",
            arn,
        )
        for arn in args.lambda_arn
    ):
        parser.error("Expected exact Lambda function ARN(s), not wildcards")
    path = STATE_DIR / "gateway-permissions.json"
    save(
        path,
        {
            "gatewayName": args.gateway,
            "providers": providers,
            "lambdaArns": args.lambda_arn,
        },
    )
    print(
        f"Wrote {path}. Review `agentcore build`, then deploy before creating SDK targets."
    )


if __name__ == "__main__":
    main()
