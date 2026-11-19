"""
Deploy notification MCP tools (Lambda + AgentCore Gateway targets).

Tools deployed:
  email_send_tool     — send_email, send_bulk_email
  email_template_tool — get_template, list_templates, create_template
  sms_notify_tool     — send_sms, send_bulk_sms
"""
import io
import time
import zipfile
import pathlib

_HERE   = pathlib.Path(__file__).parent
_LAMBDA = (_HERE.parent / "tools" / "notification.py").read_text()
_DB     = (_HERE.parent / "db.py").read_text()


def _make_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("lambda_function.py", _LAMBDA)
        zf.writestr("db.py", _DB)
    return buf.getvalue()


def _wait_lambda(client, name):
    while client.get_function(FunctionName=name)["Configuration"]["State"] != "Active":
        time.sleep(2)


def _wait_target(agentcore_client, gateway_id, target_id):
    while agentcore_client.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id)["status"] != "READY":
        time.sleep(5)


def _create_target(agentcore_client, gateway_id, name, lambda_arn, tools):
    tid = agentcore_client.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=name,
        targetConfiguration={"mcp": {"lambda": {
            "lambdaArn": lambda_arn,
            "toolSchema": {"inlinePayload": tools}
        }}},
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
    )["targetId"]
    _wait_target(agentcore_client, gateway_id, tid)
    return tid


def deploy(*, lambda_client, agentcore_client, lambda_role_arn,
           gateway_id, gateway_role_arn, table_names, timestamp):
    """
    Deploy notification Lambda + 3 gateway targets.

    Returns:
        dict with lambda_fn_name, lambda_arn, targets
              (keys: email_send, email_template, sms_notify)
    """
    fn_name = f"notification-mcp-{timestamp}"
    env = {
        "TEMPLATES_TABLE": table_names["templates"],
    }

    print("Deploying notification Lambda...")
    lambda_arn = lambda_client.create_function(
        FunctionName=fn_name, Runtime="python3.13", Role=lambda_role_arn,
        Handler="lambda_function.lambda_handler", Code={"ZipFile": _make_zip()},
        Timeout=30, Environment={"Variables": env},
        Description="Notification MCP Lambda",
    )["FunctionArn"]
    _wait_lambda(lambda_client, fn_name)
    lambda_client.add_permission(
        FunctionName=fn_name, StatementId=f"gateway-invoke-{timestamp}",
        Action="lambda:InvokeFunction", Principal=gateway_role_arn,
    )
    print(f"  Lambda ready : {lambda_arn}")

    print("Creating gateway targets...")
    email_send_id = _create_target(agentcore_client, gateway_id,
        f"email-send-{timestamp}", lambda_arn, [
        {"name": "send_email",
         "description": "Send a transactional email to a recipient",
         "inputSchema": {"type": "object",
                         "properties": {
                             "to":          {"type": "string", "description": "Recipient email"},
                             "subject":     {"type": "string"},
                             "body":        {"type": "string"},
                             "template_id": {"type": "string", "description": "Optional template ID"},
                             "template_vars": {"type": "object", "description": "Variables for template substitution"}
                         },
                         "required": ["to"]}},
        {"name": "send_bulk_email",
         "description": "Send the same email to a list of recipients",
         "inputSchema": {"type": "object",
                         "properties": {
                             "recipients": {"type": "array", "items": {"type": "string"}},
                             "subject":    {"type": "string"},
                             "body":       {"type": "string"}
                         },
                         "required": ["recipients", "subject", "body"]}},
    ])
    print(f"  email_send_tool     ready : {email_send_id}")

    email_template_id = _create_target(agentcore_client, gateway_id,
        f"email-template-{timestamp}", lambda_arn, [
        {"name": "get_template",
         "description": "Fetch an email template by ID",
         "inputSchema": {"type": "object",
                         "properties": {"template_id": {"type": "string"}},
                         "required": ["template_id"]}},
        {"name": "list_templates",
         "description": "List all available email templates",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "create_template",
         "description": "Create a new email template",
         "inputSchema": {"type": "object",
                         "properties": {
                             "template_id": {"type": "string"},
                             "subject":     {"type": "string"},
                             "body":        {"type": "string"}
                         },
                         "required": ["subject", "body"]}},
    ])
    print(f"  email_template_tool ready : {email_template_id}")

    sms_id = _create_target(agentcore_client, gateway_id,
        f"sms-notify-{timestamp}", lambda_arn, [
        {"name": "send_sms",
         "description": "Send an SMS to a single recipient",
         "inputSchema": {"type": "object",
                         "properties": {
                             "to":   {"type": "string", "description": "E.164 phone number"},
                             "body": {"type": "string", "description": "SMS body (max 160 chars)"}
                         },
                         "required": ["to", "body"]}},
        {"name": "send_bulk_sms",
         "description": "Send an SMS to multiple recipients",
         "inputSchema": {"type": "object",
                         "properties": {
                             "recipients": {"type": "array", "items": {"type": "string"}},
                             "body":       {"type": "string"}
                         },
                         "required": ["recipients", "body"]}},
    ])
    print(f"  sms_notify_tool     ready : {sms_id}")

    return {
        "lambda_fn_name": fn_name,
        "lambda_arn":     lambda_arn,
        "targets": {
            "email_send":     email_send_id,
            "email_template": email_template_id,
            "sms_notify":     sms_id,
        },
    }
