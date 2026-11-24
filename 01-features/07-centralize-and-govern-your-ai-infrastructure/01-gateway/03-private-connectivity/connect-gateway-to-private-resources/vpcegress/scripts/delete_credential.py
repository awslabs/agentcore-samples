"""Delete a lab-owned provider only after confirming no gateway references it."""
import argparse
import json
import time

from botocore.exceptions import ClientError

from lifecycle import STATE_DIR, load, not_found, record_path, save, verified_session
from credential_generation import verify_generation


def pages(control, operation, **kwargs):
    while True:
        page = getattr(control, operation)(**kwargs)
        yield from page.get("items", [])
        token = page.get("nextToken")
        if not token:
            return
        kwargs["nextToken"] = token


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--type", required=True, choices=["oauth", "api-key"])
    args = parser.parse_args(argv)
    try:
        path = record_path("credential", args.name)
        record = load(path)
        if record.get("type") != args.type or not record.get("arn"):
            raise ValueError("Missing or conflicting credential ownership record")
        if record.get('status') == 'DELETED':
            print('This recorded provider was already deleted; not touching a possibly reused name.')
            return
        if not record.get('secretArn') or not record.get('createdTime'):
            raise ValueError('Missing provider generation evidence; reconcile the original creation, never adopt a current name')
        for target_path in STATE_DIR.glob("target-*.json"):
            target = load(target_path)
            if record["arn"] in json.dumps(target.get("request", {})) and target.get("status") != "DELETED":
                raise ValueError(f"Delete and confirm target absence first: {target_path}")
    except (ValueError, OSError) as error:
        parser.error(str(error))
    control = verified_session(record).client("bedrock-agentcore-control")
    # A provider may have been attached outside this local run. Check all pages,
    # fail on AccessDenied, and never equate an incomplete inventory with absence.
    for gateway in pages(control, "list_gateways", maxResults=100):
        for target in pages(control, "list_gateway_targets", gatewayIdentifier=gateway["gatewayId"], maxResults=100):
            detail = control.get_gateway_target(gatewayIdentifier=gateway["gatewayId"], targetId=target["targetId"])
            if record["arn"] in json.dumps(detail.get("credentialProviderConfigurations", [])):
                raise RuntimeError(f"Provider still referenced by {gateway['gatewayId']}/{target['targetId']}")
    kind = "oauth2" if args.type == "oauth" else "api_key"
    get = getattr(control, f"get_{kind}_credential_provider")
    try:
        current = get(name=args.name)
        verify_generation(record, current)
        intent = record.get('deleteIntent', {})
        if not intent or intent.get('state') == 'REJECTED':
            # The API deletes by name, with no conditional generation parameter.
            # Recheck generation immediately before the call, journal intent, and
            # never repeat an accepted/uncertain name-based delete on a retry.
            record['deleteIntent'] = {'state': 'INTENT_RECORDED', 'secretArn': record['secretArn'],
                                      'createdTime': record['createdTime']}
            record['status'] = 'DELETING'
            save(path, record)
            try:
                getattr(control, f"delete_{kind}_credential_provider")(name=args.name)
            except ClientError as error:
                if error.response['Error']['Code'] == 'AccessDeniedException':
                    record['deleteIntent']['state'] = 'REJECTED'
                    save(path, record)
                raise
            record['deleteIntent']['state'] = 'ACCEPTED'
            save(path, record)
    except ClientError as error:
        if not not_found(error):
            raise
    for attempt in range(30):
        try:
            current = get(name=args.name)
            verify_generation(record, current)
        except ClientError as error:
            if not not_found(error):
                raise
            record["status"] = "DELETED"
            save(path, record)
            print(f"Confirmed provider absence. Audit record retained: {path}")
            return
        if attempt < 29:
            time.sleep(10)
    raise TimeoutError(f"Provider deletion not confirmed; keep {path}. An accepted/uncertain delete is only polled, not repeated.")


if __name__ == "__main__":
    main()
