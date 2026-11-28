"""Delete only a target recorded by this lab and wait for confirmed absence."""

import argparse
import os

from botocore.exceptions import ClientError
from lifecycle import load, not_found, record_path, save, verified_session, wait_target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    try:
        path = record_path("target", args.name)
        record = load(path)
        if not record.get("targetId"):
            raise ValueError(
                f"Create response was not recorded; reconcile {path} before cleanup"
            )
        if record["gatewayId"] != os.environ.get("GATEWAY_ID"):
            raise ValueError("GATEWAY_ID differs from the ownership record")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    control = verified_session(record).client("bedrock-agentcore-control")
    # Use the exact recorded ID, never a first-page/name search or adoption.
    try:
        control.delete_gateway_target(
            gatewayIdentifier=record["gatewayId"], targetId=record["targetId"]
        )
    except ClientError as error:
        if not not_found(error):
            raise
    record["status"] = "DELETING"
    save(path, record)
    wait_target(control, record, path, deleting=True)
    print(
        f"Confirmed target absence: {record['targetId']}. Audit record retained: {path}"
    )


if __name__ == "__main__":
    main()
