"""
Deploy order management MCP tools (Lambda + AgentCore Gateway targets).

Tools deployed:
  order_lookup_tool  — get_order, list_orders
  order_update_tool  — update_order_status, update_shipping_addr
  order_cancel_tool  — cancel_order
"""
import io
import time
import zipfile
import pathlib

_HERE   = pathlib.Path(__file__).parent
_LAMBDA = (_HERE.parent / "tools" / "order_management.py").read_text()
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
    Deploy order management Lambda + 3 gateway targets.

    Returns:
        dict with lambda_fn_name, lambda_arn, targets
              (keys: order_lookup, order_update, order_cancel)
    """
    fn_name = f"order-mgmt-mcp-{timestamp}"
    env = {
        "ORDERS_TABLE":    table_names["orders"],
        "CUSTOMERS_TABLE": table_names["customers"],
        "SHIPMENTS_TABLE": table_names["shipments"],
        "PAYMENTS_TABLE":  table_names.get("payments", ""),
    }

    print("Deploying order management Lambda...")
    lambda_arn = lambda_client.create_function(
        FunctionName=fn_name, Runtime="python3.13", Role=lambda_role_arn,
        Handler="lambda_function.lambda_handler", Code={"ZipFile": _make_zip()},
        Timeout=30, Environment={"Variables": env},
        Description="Order management MCP Lambda",
    )["FunctionArn"]
    _wait_lambda(lambda_client, fn_name)
    lambda_client.add_permission(
        FunctionName=fn_name, StatementId=f"gateway-invoke-{timestamp}",
        Action="lambda:InvokeFunction", Principal=gateway_role_arn,
    )
    print(f"  Lambda ready : {lambda_arn}")

    print("Creating gateway targets...")
    lookup_id = _create_target(agentcore_client, gateway_id,
        f"order-lookup-{timestamp}", lambda_arn, [
        {"name": "get_order",
         "description": "Fetch full order details by order ID",
         "inputSchema": {"type": "object",
                         "properties": {"order_id": {"type": "string", "description": "Order ID e.g. ORD-1001"}},
                         "required": ["order_id"]}},
        {"name": "list_orders",
         "description": "List orders, optionally filtered by customer email or status",
         "inputSchema": {"type": "object",
                         "properties": {
                             "customer_email": {"type": "string", "description": "Filter by customer email"},
                             "status":         {"type": "string", "description": "PENDING|PROCESSING|SHIPPED|DELIVERED|CANCELLED"}
                         }}},
    ])
    print(f"  order_lookup_tool  ready : {lookup_id}")

    update_id = _create_target(agentcore_client, gateway_id,
        f"order-update-{timestamp}", lambda_arn, [
        {"name": "update_order_status",
         "description": "Change the status of an order",
         "inputSchema": {"type": "object",
                         "properties": {
                             "order_id": {"type": "string"},
                             "status":   {"type": "string", "description": "PENDING|PROCESSING|SHIPPED|DELIVERED|CANCELLED|RETURNED"}
                         },
                         "required": ["order_id", "status"]}},
        {"name": "update_shipping_addr",
         "description": "Update delivery address before an order ships",
         "inputSchema": {"type": "object",
                         "properties": {
                             "order_id": {"type": "string"},
                             "street":   {"type": "string"},
                             "city":     {"type": "string"},
                             "state":    {"type": "string"},
                             "zip":      {"type": "string"}
                         },
                         "required": ["order_id", "street", "city", "state", "zip"]}},
    ])
    print(f"  order_update_tool  ready : {update_id}")

    cancel_id = _create_target(agentcore_client, gateway_id,
        f"order-cancel-{timestamp}", lambda_arn, [
        {"name": "cancel_order",
         "description": "Cancel an order by ID; triggers refund if payment was captured",
         "inputSchema": {"type": "object",
                         "properties": {
                             "order_id": {"type": "string"},
                             "reason":   {"type": "string", "description": "Cancellation reason (default: customer_request)"}
                         },
                         "required": ["order_id"]}},
    ])
    print(f"  order_cancel_tool  ready : {cancel_id}")

    return {
        "lambda_fn_name": fn_name,
        "lambda_arn":     lambda_arn,
        "targets": {
            "order_lookup": lookup_id,
            "order_update": update_id,
            "order_cancel": cancel_id,
        },
    }
