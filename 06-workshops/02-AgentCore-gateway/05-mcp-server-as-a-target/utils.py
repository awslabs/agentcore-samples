import boto3
import json
import subprocess
import time
import requests


def get_agent_status(agent_name: str, cwd: str = "mcpservers") -> dict:
    """Run `agentcore status --json` and return the resource entry for the named agent.

    Uses `JSONDecoder.raw_decode` to ignore the trailing ANSI cursor-show
    escape that Ink leaks on stdout even in --json mode.
    """
    result = subprocess.run(
        ["agentcore", "status", "--json"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    status, _ = json.JSONDecoder().raw_decode(result.stdout.lstrip())
    return next(
        r
        for r in status["resources"]
        if r["resourceType"] == "agent" and r["name"] == agent_name
    )


def deploy_cognito_stack(cfn, stack_name: str, template_path: str) -> dict:
    """Idempotently deploy the Cognito CloudFormation stack; return its outputs.

    Creates the stack if missing, attempts an update if it's already in a
    `*_COMPLETE` state (swallowing "no updates" errors), and raises if the
    stack is in any non-terminal state.
    """
    with open(template_path) as f:
        template_body = f.read()

    def _stack_status(name):
        try:
            return cfn.describe_stacks(StackName=name)["Stacks"][0]["StackStatus"]
        except cfn.exceptions.ClientError as e:
            if "does not exist" in str(e):
                return None
            raise

    status = _stack_status(stack_name)

    if status is None:
        print(f"Creating stack {stack_name}...")
        cfn.create_stack(
            StackName=stack_name,
            TemplateBody=template_body,
            Capabilities=[],  # stack has no IAM resources
            OnFailure="DELETE",
        )
        cfn.get_waiter("stack_create_complete").wait(StackName=stack_name)
    elif status.endswith("_COMPLETE"):
        try:
            print(f"Stack {stack_name} exists ({status}); attempting update...")
            cfn.update_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Capabilities=[],
            )
            cfn.get_waiter("stack_update_complete").wait(StackName=stack_name)
        except cfn.exceptions.ClientError as e:
            if "No updates are to be performed" not in str(e):
                raise
            print("No stack updates needed.")
    else:
        raise RuntimeError(
            f"Stack {stack_name} is in non-terminal state {status}; resolve before continuing."
        )

    return {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=stack_name)["Stacks"][0]["Outputs"]
    }


def delete_iam_role(role_name: str) -> None:
    """Delete an IAM role plus any attached managed and inline policies. Idempotent."""
    iam = boto3.client("iam")
    try:
        for p in iam.list_attached_role_policies(RoleName=role_name)[
            "AttachedPolicies"
        ]:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
        for name in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
            iam.delete_role_policy(RoleName=role_name, PolicyName=name)
        iam.delete_role(RoleName=role_name)
        print(f"✓ Deleted IAM role: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"ℹ️  IAM role not found: {role_name}")


def get_token(
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    scope_string: str,
) -> dict:
    """Mint a client_credentials access token from `token_endpoint` (the
    Cognito hosted-UI `/oauth2/token` URL — read this from the stack's
    `TokenEndpoint` output)."""
    try:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope_string,
        }
        response = requests.post(
            token_endpoint, headers=headers, data=data, timeout=3600
        )
        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as err:
        return {"error": str(err)}


def create_agentcore_gateway_role_with_region(gateway_name, region):
    """
    Create an IAM role for AgentCore Gateway with explicit region specification.

    Args:
        gateway_name: Name of the gateway
        region: AWS region where the gateway will be deployed

    Returns:
        IAM role response
    """
    iam_client = boto3.client("iam")
    agentcore_gateway_role_name = f"agentcore-{gateway_name}-role"
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "VisualEditor0",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:*",
                    "bedrock:*",
                    "agent-credential-provider:*",
                    "iam:PassRole",
                    "secretsmanager:GetSecretValue",
                    "lambda:InvokeFunction",
                ],
                "Resource": "*",
            }
        ],
    }

    assume_role_policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": f"{account_id}"},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"
                    },
                },
            }
        ],
    }

    assume_role_policy_document_json = json.dumps(assume_role_policy_document)
    role_policy_document = json.dumps(role_policy)

    try:
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_gateway_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )
        time.sleep(10)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print("Role already exists -- deleting and creating it again")
        policies = iam_client.list_role_policies(
            RoleName=agentcore_gateway_role_name, MaxItems=100
        )
        print("policies:", policies)
        for policy_name in policies["PolicyNames"]:
            iam_client.delete_role_policy(
                RoleName=agentcore_gateway_role_name, PolicyName=policy_name
            )
        print(f"deleting {agentcore_gateway_role_name}")
        iam_client.delete_role(RoleName=agentcore_gateway_role_name)
        print(f"recreating {agentcore_gateway_role_name}")
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_gateway_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )

    print(f"attaching role policy {agentcore_gateway_role_name}")
    try:
        iam_client.put_role_policy(
            PolicyDocument=role_policy_document,
            PolicyName="AgentCorePolicy",
            RoleName=agentcore_gateway_role_name,
        )
    except Exception as e:
        print(e)

    return agentcore_iam_role


def delete_gateway(gateway_client, gatewayId):
    print("Deleting all targets for gateway", gatewayId)
    list_response = gateway_client.list_gateway_targets(
        gatewayIdentifier=gatewayId, maxResults=100
    )
    for item in list_response["items"]:
        targetId = item["targetId"]
        print("Deleting target ", targetId)
        gateway_client.delete_gateway_target(
            gatewayIdentifier=gatewayId, targetId=targetId
        )
        time.sleep(5)
    print("Deleting gateway ", gatewayId)
    gateway_client.delete_gateway(gatewayIdentifier=gatewayId)


def interactive_input_form(params):
    """Schema-driven prompt-via-input() callback. Each field in
    `requestedSchema.properties` becomes one `input()` prompt with hints
    derived from the schema (enum options, integer ranges, boolean y/N).
    Type `d` to decline or `c` to cancel at any prompt.
    """
    message = params.get("message") or "Please provide input"
    schema = params.get("requestedSchema") or {}
    props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
    print(f"\n>>> {message}")
    data = {}
    for name, field in props.items():
        ftype = field.get("type") if isinstance(field, dict) else None
        enum = field.get("enum") if isinstance(field, dict) else None
        if ftype == "boolean":
            prompt = f"  {name} [y/N]: "
        elif enum:
            prompt = f"  {name} [{'/'.join(map(str, enum))}]: "
        elif ftype == "integer":
            mn = field.get("minimum") if isinstance(field, dict) else None
            mx = field.get("maximum") if isinstance(field, dict) else None
            range_str = (
                f"[{mn}-{mx}]" if mn is not None and mx is not None else "(integer)"
            )
            prompt = f"  {name} {range_str}: "
        elif ftype == "number":
            prompt = f"  {name} (number): "
        else:
            prompt = f"  {name}: "

        raw = input(prompt).strip()
        if raw.lower() in ("d", "decline"):
            print("  -> declined")
            return {"action": "decline"}
        if raw.lower() in ("c", "cancel"):
            print("  -> cancelled")
            return {"action": "cancel"}

        if ftype == "string":
            data[name] = raw if raw else (enum[0] if enum else "")
        elif ftype == "integer":
            try:
                data[name] = int(raw) if raw else (field.get("minimum") or 0)
            except ValueError:
                data[name] = field.get("minimum") or 0
        elif ftype == "number":
            try:
                data[name] = float(raw) if raw else 0.0
            except ValueError:
                data[name] = 0.0
        elif ftype == "boolean":
            data[name] = raw.lower() in ("y", "yes", "true", "1")
        else:
            data[name] = raw

    return {"action": "accept", "content": data}


def bedrock_sampling(
    params,
    model_id: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    region: str | None = None,
):
    """Real sampling callback — delegates to Amazon Bedrock via the Converse
    API. Pass directly to `GatewayMCPClient.call_tool_streaming(
    sampling_callback=...)`.

    Translates MCP `sampling/createMessage` params into Bedrock Converse
    inputs, then translates the response back into a `CreateMessageResult`-
    shaped dict.

    The IAM principal running this notebook must have `bedrock:InvokeModel`
    on `model_id`.
    """
    bedrock = boto3.client("bedrock-runtime", region_name=region)

    # MCP sends `messages` as either a string (when the server passes
    # `messages="..."` to `ctx.sample`) or a list of {role, content} dicts.
    raw_messages = params.get("messages")
    if isinstance(raw_messages, str):
        raw_messages = [
            {"role": "user", "content": {"type": "text", "text": raw_messages}}
        ]
    elif raw_messages is None:
        raw_messages = []

    converse_messages = []
    for m in raw_messages:
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, dict):
            text = content.get("text", "")
        else:
            text = str(content)
        converse_messages.append({"role": role, "content": [{"text": text}]})

    inference_cfg: dict = {"maxTokens": int(params.get("maxTokens") or 256)}
    if params.get("temperature") is not None:
        inference_cfg["temperature"] = float(params["temperature"])
    if params.get("stopSequences"):
        inference_cfg["stopSequences"] = list(params["stopSequences"])

    kwargs = {
        "modelId": model_id,
        "messages": converse_messages,
        "inferenceConfig": inference_cfg,
    }
    if params.get("systemPrompt"):
        kwargs["system"] = [{"text": params["systemPrompt"]}]

    response = bedrock.converse(**kwargs)
    text = response["output"]["message"]["content"][0]["text"]

    return {
        "role": "assistant",
        "content": {"type": "text", "text": text},
        "model": model_id,
        "stopReason": response.get("stopReason"),
    }


def show(label, outcome):
    """Pretty-print whatever `call_tool_streaming` returned."""
    result = outcome.get("result") or {}
    error = outcome.get("error")
    print(f"--- {label} ---")
    print(f"  isError: {result.get('isError') if result else None}")
    if error:
        print(f"  error: {error}")
    for c in result.get("content") or []:
        print(f"  content: {c.get('text', c)}")
