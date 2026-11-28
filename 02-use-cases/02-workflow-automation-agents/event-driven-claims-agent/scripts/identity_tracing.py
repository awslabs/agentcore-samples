"""Optional workload-identity deliveries, journaled by exact IDs after deployment.

These IDs are only available from Runtime/Gateway control-plane responses. The
normal Runtime/Gateway/Memory log/trace resources are CloudFormation-owned.
No account-level observability settings are changed here.
"""

from lifecycle import OWNER_TAG, absent, assert_owner, pages, save, stack_or_none


def setup(root, state, clients):
    if state["phase"] != "deployed" or state.get("identity_tracing"):
        raise ValueError("Identity tracing requires a deployed stack with no prior tracing journal")
    cfn = clients("cloudformation")
    stack = stack_or_none(cfn, state["stack_arn"])
    if not stack:
        raise ValueError("Owned stack missing")
    assert_owner({t["Key"]: t["Value"] for t in stack.get("Tags", [])}, state)
    control, logs = clients("bedrock-agentcore-control"), clients("logs")
    identities = []
    for resource in pages(cfn, "list_stack_resources", "StackResourceSummaries", StackName=state["stack_arn"]):
        physical = resource.get("PhysicalResourceId", "").split("/")[-1]
        if resource["ResourceType"] == "AWS::BedrockAgentCore::Runtime":
            response = control.get_agent_runtime(agentRuntimeId=physical)
            kind = "runtime"
        elif resource["ResourceType"] == "AWS::BedrockAgentCore::Gateway":
            response = control.get_gateway(gatewayIdentifier=physical)
            kind = "gateway"
        else:
            continue
        identities.append((kind, response["workloadIdentityDetails"]["workloadIdentityArn"]))
    if {kind for kind, _ in identities} != {"runtime", "gateway"}:
        raise ValueError("Expected Runtime and Gateway workload identities")
    entries = []
    prefix = f"{state['project']}-{state['target']}"
    # Preflight ALL names before creating anything. Existing names are not ownership.
    for kind, arn in identities:
        group = f"/aws/vendedlogs/bedrock-agentcore/{prefix}/{kind}-identity"
        if any(
            g["logGroupName"] == group
            for g in pages(logs, "describe_log_groups", "logGroups", token="nextToken", logGroupNamePrefix=group)
        ):
            raise ValueError("Identity log-group name collision")
        for log_type, destination_type in [("APPLICATION_LOGS", "CWL"), ("TRACES", "XRAY")]:
            name = f"{prefix}-{kind}-identity-{destination_type}"
            if absent(logs.get_delivery_source, name=name) or absent(logs.get_delivery_destination, name=name):
                raise ValueError("Identity delivery name collision")
            entries.append(
                {
                    "name": name,
                    "resource_arn": arn,
                    "kind": kind,
                    "log_type": log_type,
                    "destination_type": destination_type,
                    "log_group": group if destination_type == "CWL" else None,
                }
            )
    state["identity_tracing"] = entries
    state["phase"] = "tracing-incomplete"
    save(root, state)
    for entry in entries:
        if entry["log_group"]:
            logs.create_log_group(logGroupName=entry["log_group"], tags={OWNER_TAG: state["owner"]})
            entry["log_group_created"] = True
            save(root, state)
            logs.put_retention_policy(logGroupName=entry["log_group"], retentionInDays=7)
        source = logs.put_delivery_source(
            name=entry["name"],
            resourceArn=entry["resource_arn"],
            logType=entry["log_type"],
            tags={OWNER_TAG: state["owner"]},
        )["deliverySource"]
        entry["source_arn"] = source["arn"]
        save(root, state)
        destination_args = {
            "name": entry["name"],
            "deliveryDestinationType": entry["destination_type"],
            "tags": {OWNER_TAG: state["owner"]},
        }
        if entry["log_group"]:
            destination_args["deliveryDestinationConfiguration"] = {
                "destinationResourceArn": f"arn:aws:logs:{state['region']}:{state['account']}:log-group:{entry['log_group']}"
            }
        destination = logs.put_delivery_destination(**destination_args)["deliveryDestination"]
        entry["destination_arn"] = destination["arn"]
        save(root, state)
        delivery = logs.create_delivery(
            deliverySourceName=entry["name"],
            deliveryDestinationArn=entry["destination_arn"],
            tags={OWNER_TAG: state["owner"]},
        )["delivery"]
        entry["delivery_id"] = delivery["id"]
        entry["delivery_arn"] = delivery["arn"]
        save(root, state)
    state["workload_identity_arns"] = sorted({arn for _, arn in identities})
    state["phase"] = "deployed"
    save(root, state)


def cleanup(root, state, clients):
    entries = state.get("identity_tracing", [])
    if not entries:
        return
    logs = clients("logs")
    # Journal completeness matters: an uncertain create must not be adopted.
    if any(not e.get("delivery_id") for e in entries):
        raise ValueError("Partial identity tracing journal; exact-ID recovery required")
    for entry in entries:
        delivery = absent(logs.get_delivery, id=entry["delivery_id"])
        if delivery:
            actual = delivery["delivery"]
            if actual["arn"] != entry["delivery_arn"] or actual["deliveryDestinationArn"] != entry["destination_arn"]:
                raise ValueError("Identity delivery ARN mismatch")
            assert_owner(logs.list_tags_for_resource(resourceArn=entry["delivery_arn"])["tags"], state)
            logs.delete_delivery(id=entry["delivery_id"])
        if absent(logs.get_delivery, id=entry["delivery_id"]) is not None:
            raise ValueError("Identity delivery deletion not yet verified")
        for kind in ("source", "destination"):
            getter = getattr(logs, f"get_delivery_{kind}")
            found = absent(getter, name=entry["name"])
            if found:
                actual = found["deliverySource" if kind == "source" else "deliveryDestination"]
                if actual["arn"] != entry[f"{kind}_arn"]:
                    raise ValueError("Identity delivery resource ARN mismatch")
                assert_owner(logs.list_tags_for_resource(resourceArn=actual["arn"])["tags"], state)
                getattr(logs, f"delete_delivery_{kind}")(name=entry["name"])
            if absent(getter, name=entry["name"]) is not None:
                raise ValueError("Identity delivery resource deletion not yet verified")
        if entry.get("log_group_created"):
            groups = list(
                pages(
                    logs, "describe_log_groups", "logGroups", token="nextToken", logGroupNamePrefix=entry["log_group"]
                )
            )
            matches = [g for g in groups if g["logGroupName"] == entry["log_group"]]
            if matches:
                # list_tags_log_group returns tags for the exact group, not its ARN wildcard.
                assert_owner(logs.list_tags_log_group(logGroupName=entry["log_group"])["tags"], state)
                logs.delete_log_group(logGroupName=entry["log_group"])
            if any(
                g["logGroupName"] == entry["log_group"]
                for g in pages(
                    logs, "describe_log_groups", "logGroups", token="nextToken", logGroupNamePrefix=entry["log_group"]
                )
            ):
                raise ValueError("Identity log group deletion not yet verified")
        entry["deleted"] = True
        save(root, state)
