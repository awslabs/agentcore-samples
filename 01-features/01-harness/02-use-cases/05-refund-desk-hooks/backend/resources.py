"""AWS resource lifecycle — create or reuse everything the demo needs.

  Lambda hook functions (x4) + their execution role
  SNS topic ──► SQS queue      (audit_tool_calls hook, read by the web app)
  EventBridge bus + rule ──► same SQS queue   (usage_meter hook)
  Harness execution role (model access + permission to call each hook target)
  Harness with inline tools and all six lifecycle hooks

State is saved to resource_info.json after every step, so ./cleanup.sh can remove
a partially provisioned stack too.
"""

import io
import json
import secrets
import time
import uuid
import zipfile
from pathlib import Path

from agent import MODEL_ID, SYSTEM_PROMPT
from botocore.exceptions import ClientError
from clients import REGION, agentcore_control_client, client
from hooks import build_hooks, default_settings, wait_for_harness
from tools import TOOL_SPECS

ROOT = Path(__file__).parent.parent
STATE_FILE = ROOT / "resource_info.json"
LAMBDA_DIR = ROOT / "lambdas"

LAMBDA_FUNCTIONS = {
    # hook name -> (source file, extra environment)
    "screen_request": ("screen_request.py", {"CHAOS_MODE": "off"}),
    "refund_policy": ("refund_policy.py", {}),
    "validate_result": ("validate_result.py", {}),  # RECEIPT_SECRET added at deploy
    "token_budget": ("token_budget.py", {"OUTPUT_TOKEN_BUDGET": "300"}),
}

LAMBDA_TRUST = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}],
}


def _log(msg: str):
    print(f"[resources] {msg}", flush=True)


def load_state() -> dict | None:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _account_id() -> str:
    return client("sts").get_caller_identity()["Account"]


def _harness_trust(account_id: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
            }
        ],
    }


def _harness_permissions(state: dict) -> dict:
    lambda_arns = [state[f"{name}_arn"] for name in LAMBDA_FUNCTIONS]
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BedrockInvokeModel",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    "arn:aws:bedrock:*:*:inference-profile/*",
                ],
            },
            # Needed when the account enforces a Bedrock guardrail on every model call.
            {
                "Sid": "AccountEnforcedGuardrails",
                "Effect": "Allow",
                "Action": "bedrock:ApplyGuardrail",
                "Resource": f"arn:aws:bedrock:*:{state['account_id']}:guardrail/*",
            },
            {
                "Sid": "Observability",
                "Effect": "Allow",
                "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                "Resource": "*",
            },
            {
                "Sid": "HarnessLogs",
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": [
                    "arn:aws:logs:*:*:log-group:/aws/bedrock-agentcore/runtimes/*",
                    "arn:aws:logs:*:*:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
                ],
            },
            # The harness creates an AgentCore Memory for session history and reads it every turn.
            {
                "Sid": "HarnessMemory",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                ],
                "Resource": f"arn:aws:bedrock-agentcore:*:{state['account_id']}:memory/*",
            },
            # Lifecycle hook targets — scoped to the exact ARNs used in the hook config.
            {
                "Sid": "InvokeLifecycleHookLambda",
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": lambda_arns,
            },
            {
                "Sid": "PublishLifecycleHookSns",
                "Effect": "Allow",
                "Action": "sns:Publish",
                "Resource": state["audit_topic_arn"],
            },
            {
                "Sid": "PublishLifecycleHookEventBridge",
                "Effect": "Allow",
                "Action": "events:PutEvents",
                "Resource": state["event_bus_arn"],
            },
        ],
    }


def _zip_lambda(source_file: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(LAMBDA_DIR / source_file, source_file)
        zf.write(LAMBDA_DIR / "orders.json", "orders.json")
    return buffer.getvalue()


def _retry(fn, what: str, attempts: int = 12, delay: int = 5):
    """Retry calls that fail while a freshly created IAM role propagates."""
    for attempt in range(attempts):
        try:
            return fn()
        except ClientError as e:
            code = e.response["Error"]["Code"]
            message = e.response["Error"]["Message"]
            retryable = code in ("InvalidParameterValueException", "ValidationException", "AccessDeniedException")
            if not retryable or attempt == attempts - 1:
                raise
            _log(f"  {what}: waiting for IAM propagation ({message[:90]})")
            time.sleep(delay)


def resources_alive(state: dict) -> bool:
    try:
        if agentcore_control_client().get_harness(harnessId=state["harness_id"])["harness"]["status"] != "READY":
            return False
        lam = client("lambda")
        for name in LAMBDA_FUNCTIONS:
            lam.get_function(FunctionName=state[f"{name}_arn"])
        client("sqs").get_queue_attributes(QueueUrl=state["queue_url"], AttributeNames=["QueueArn"])
        return True
    except (ClientError, KeyError):
        return False


def ensure_resources() -> dict:
    existing = load_state()
    if existing and existing.get("harness_id") and resources_alive(existing):
        _log("Reusing existing resources")
        return existing
    if existing:
        _log("Found stale resource_info.json — removing the old stack first")
        destroy_resources()

    suffix = uuid.uuid4().hex[:6]
    account_id = _account_id()
    state: dict = {"suffix": suffix, "region": REGION, "account_id": account_id}
    save_state(state)

    iam = client("iam")
    lam = client("lambda")
    sns = client("sns")
    sqs = client("sqs")
    events = client("events")

    # ── Lambda execution role ────────────────────────────────────────────────
    lambda_role = f"RefundDeskHookLambda-{suffix}"
    resp = iam.create_role(RoleName=lambda_role, AssumeRolePolicyDocument=json.dumps(LAMBDA_TRUST))
    iam.attach_role_policy(
        RoleName=lambda_role,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    state.update(lambda_role_name=lambda_role, lambda_role_arn=resp["Role"]["Arn"])
    save_state(state)
    _log(f"Created Lambda role {lambda_role}")

    # ── Notification plumbing: SNS + EventBridge → one SQS queue ─────────────
    queue_name = f"refund-desk-hook-feed-{suffix}"
    queue_url = sqs.create_queue(QueueName=queue_name, Attributes={"MessageRetentionPeriod": "3600"})["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    state.update(queue_url=queue_url, queue_arn=queue_arn)
    save_state(state)

    topic_arn = sns.create_topic(Name=f"refund-desk-tool-audit-{suffix}")["TopicArn"]
    state["audit_topic_arn"] = topic_arn
    save_state(state)

    bus_name = f"refund-desk-hooks-{suffix}"
    bus_arn = events.create_event_bus(Name=bus_name)["EventBusArn"]
    state.update(event_bus_name=bus_name, event_bus_arn=bus_arn)
    save_state(state)

    rule_name = f"refund-desk-hook-events-{suffix}"
    rule_arn = events.put_rule(
        Name=rule_name,
        EventBusName=bus_name,
        EventPattern=json.dumps({"source": ["bedrock-agentcore.harness"]}),
        State="ENABLED",
    )["RuleArn"]
    state.update(event_rule_name=rule_name, event_rule_arn=rule_arn)
    save_state(state)

    sqs.set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={
            "Policy": json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "sns.amazonaws.com"},
                            "Action": "sqs:SendMessage",
                            "Resource": queue_arn,
                            "Condition": {"ArnEquals": {"aws:SourceArn": topic_arn}},
                        },
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "events.amazonaws.com"},
                            "Action": "sqs:SendMessage",
                            "Resource": queue_arn,
                            "Condition": {"ArnEquals": {"aws:SourceArn": rule_arn}},
                        },
                    ],
                }
            )
        },
    )
    sns.subscribe(
        TopicArn=topic_arn,
        Protocol="sqs",
        Endpoint=queue_arn,
        Attributes={"RawMessageDelivery": "true"},
    )
    events.put_targets(Rule=rule_name, EventBusName=bus_name, Targets=[{"Id": "hook-feed", "Arn": queue_arn}])
    _log("Created SNS topic, EventBridge bus/rule and SQS feed queue")

    # ── Hook Lambdas ─────────────────────────────────────────────────────────
    state["receipt_secret"] = secrets.token_hex(32)
    save_state(state)
    for name, (source, env) in LAMBDA_FUNCTIONS.items():
        env = dict(env)
        if name == "validate_result":
            env["RECEIPT_SECRET"] = state["receipt_secret"]
        fn_name = f"refund-desk-{name.replace('_', '-')}-{suffix}"
        create_args = {
            "FunctionName": fn_name,
            "Runtime": "python3.12",
            "Role": state["lambda_role_arn"],
            "Handler": f"{Path(source).stem}.lambda_handler",
            "Code": {"ZipFile": _zip_lambda(source)},
            "Timeout": 15,
            "MemorySize": 256,
            "Environment": {"Variables": env},
            "Description": f"Refund Desk harness lifecycle hook: {name}",
        }
        resp = _retry(lambda args=create_args: lam.create_function(**args), f"create {fn_name}")
        state[f"{name}_arn"] = resp["FunctionArn"]
        save_state(state)
        _log(f"Created Lambda {fn_name}")
    waiter = lam.get_waiter("function_active_v2")
    for name in LAMBDA_FUNCTIONS:
        waiter.wait(FunctionName=state[f"{name}_arn"])

    # ── Harness execution role ───────────────────────────────────────────────
    harness_role = f"RefundDeskHarness-{suffix}"
    resp = iam.create_role(RoleName=harness_role, AssumeRolePolicyDocument=json.dumps(_harness_trust(account_id)))
    iam.put_role_policy(
        RoleName=harness_role,
        PolicyName="RefundDeskHarnessPolicy",
        PolicyDocument=json.dumps(_harness_permissions(state)),
    )
    state.update(harness_role_name=harness_role, harness_role_arn=resp["Role"]["Arn"])
    save_state(state)
    _log(f"Created harness role {harness_role}")
    time.sleep(10)

    # ── Harness with inline tools and lifecycle hooks ────────────────────────
    settings = default_settings()
    harness_name = f"RefundDesk_{suffix}"
    resp = _retry(
        lambda: agentcore_control_client().create_harness(
            harnessName=harness_name,
            executionRoleArn=state["harness_role_arn"],
            model={"bedrockModelConfig": {"modelId": MODEL_ID}},
            systemPrompt=[{"text": SYSTEM_PROMPT}],
            tools=TOOL_SPECS,
            hooks=build_hooks(state, settings),
            maxIterations=12,
        ),
        "create harness",
    )
    harness = resp["harness"]
    state.update(
        harness_id=harness["harnessId"],
        harness_arn=harness["arn"],
        harness_name=harness_name,
        hook_settings=settings,
    )
    save_state(state)
    _log(f"Created harness {harness_name}; waiting for READY...")
    wait_for_harness(state["harness_id"])
    _log("All resources ready")
    return state


def destroy_resources():
    state = load_state()
    if not state:
        _log("No resource_info.json found")
        return

    def attempt(what, fn):
        try:
            fn()
            _log(f"Deleted {what}")
        except ClientError as e:
            if e.response["Error"]["Code"] in ("ResourceNotFoundException", "NoSuchEntity", "NotFoundException"):
                return
            _log(f"Warning deleting {what}: {e.response['Error']['Message']}")
        except Exception as e:  # noqa: BLE001 — keep going so the rest of the stack is removed
            _log(f"Warning deleting {what}: {e}")

    if state.get("harness_id"):
        control = agentcore_control_client()
        attempt(f"harness {state['harness_id']}", lambda: control.delete_harness(harnessId=state["harness_id"]))

    lam = client("lambda")
    logs = client("logs")
    for name in LAMBDA_FUNCTIONS:
        arn = state.get(f"{name}_arn")
        if arn:
            fn_name = arn.split(":")[-1]
            log_group = f"/aws/lambda/{fn_name}"
            attempt(f"Lambda {fn_name}", lambda a=arn: lam.delete_function(FunctionName=a))
            attempt(f"log group {log_group}", lambda g=log_group: logs.delete_log_group(logGroupName=g))

    events = client("events")
    if state.get("event_rule_name"):
        attempt(
            "EventBridge rule targets",
            lambda: events.remove_targets(
                Rule=state["event_rule_name"], EventBusName=state["event_bus_name"], Ids=["hook-feed"]
            ),
        )
        attempt(
            f"EventBridge rule {state['event_rule_name']}",
            lambda: events.delete_rule(Name=state["event_rule_name"], EventBusName=state["event_bus_name"]),
        )
    if state.get("event_bus_name"):
        attempt(
            f"EventBridge bus {state['event_bus_name']}", lambda: events.delete_event_bus(Name=state["event_bus_name"])
        )

    if state.get("audit_topic_arn"):
        attempt("SNS topic", lambda: client("sns").delete_topic(TopicArn=state["audit_topic_arn"]))
    if state.get("queue_url"):
        attempt("SQS queue", lambda: client("sqs").delete_queue(QueueUrl=state["queue_url"]))

    iam = client("iam")
    if state.get("harness_role_name"):
        role = state["harness_role_name"]
        attempt(
            f"inline policy on {role}",
            lambda: iam.delete_role_policy(RoleName=role, PolicyName="RefundDeskHarnessPolicy"),
        )
        attempt(f"role {role}", lambda: iam.delete_role(RoleName=role))
    if state.get("lambda_role_name"):
        role = state["lambda_role_name"]
        attempt(
            f"managed policy on {role}",
            lambda: iam.detach_role_policy(
                RoleName=role, PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            ),
        )
        attempt(f"role {role}", lambda: iam.delete_role(RoleName=role))

    STATE_FILE.unlink(missing_ok=True)
    _log("Cleanup complete")


def set_chaos_mode(state: dict, mode: str):
    """Switch the screen_request Lambda between healthy, slow (times out) and erroring."""
    lam = client("lambda")
    lam.update_function_configuration(
        FunctionName=state["screen_request_arn"],
        Environment={"Variables": {"CHAOS_MODE": mode}},
    )
    lam.get_waiter("function_updated_v2").wait(FunctionName=state["screen_request_arn"])


def get_chaos_mode(state: dict) -> str:
    config = client("lambda").get_function_configuration(FunctionName=state["screen_request_arn"])
    return config.get("Environment", {}).get("Variables", {}).get("CHAOS_MODE", "off")
