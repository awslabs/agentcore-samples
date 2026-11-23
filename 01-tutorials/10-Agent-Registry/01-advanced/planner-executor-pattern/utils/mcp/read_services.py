"""
Deploy read-only service MCP tools (Lambda + AgentCore Gateway targets).

Tools deployed:
  payment_status_tool  — get_payment_status
  inventory_check_tool — check_inventory, check_multiple_inventory
  shipping_track_tool  — track_shipment, estimate_delivery
"""
import io
import time
import zipfile
import pathlib

_HERE   = pathlib.Path(__file__).parent
_LAMBDA = (_HERE.parent / "tools" / "read_services.py").read_text()
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
    Deploy read-only services Lambda + 3 gateway targets.

    Returns:
        dict with lambda_fn_name, lambda_arn, targets
              (keys: payment_status, inventory_check, shipping_track)
    """
    fn_name = f"read-services-mcp-{timestamp}"
    env = {
        "PAYMENTS_TABLE":  table_names["payments"],
        "INVENTORY_TABLE": table_names["inventory"],
        "ORDERS_TABLE":    table_names["orders"],
        "SHIPMENTS_TABLE": table_names["shipments"],
    }

    print("Deploying read-services Lambda...")
    lambda_arn = lambda_client.create_function(
        FunctionName=fn_name, Runtime="python3.13", Role=lambda_role_arn,
        Handler="lambda_function.lambda_handler", Code={"ZipFile": _make_zip()},
        Timeout=30, Environment={"Variables": env},
        Description="Read-only services MCP Lambda",
    )["FunctionArn"]
    _wait_lambda(lambda_client, fn_name)
    lambda_client.add_permission(
        FunctionName=fn_name, StatementId=f"gateway-invoke-{timestamp}",
        Action="lambda:InvokeFunction", Principal=gateway_role_arn,
    )
    print(f"  Lambda ready : {lambda_arn}")

    print("Creating gateway targets...")
    payment_id = _create_target(agentcore_client, gateway_id,
        f"payment-status-{timestamp}", lambda_arn, [
        {"name": "get_payment_status",
         "description": "Get payment status and details for an order or by payment ID",
         "inputSchema": {"type": "object",
                         "properties": {
                             "order_id":   {"type": "string", "description": "Order ID e.g. ORD-1001"},
                             "payment_id": {"type": "string", "description": "Payment ID e.g. PAY-001"}
                         }}},
    ])
    print(f"  payment_status_tool  ready : {payment_id}")

    inventory_id = _create_target(agentcore_client, gateway_id,
        f"inventory-check-{timestamp}", lambda_arn, [
        {"name": "check_inventory",
         "description": "Check stock availability for a single SKU",
         "inputSchema": {"type": "object",
                         "properties": {"sku": {"type": "string", "description": "Product SKU e.g. WIDGET-42"}},
                         "required": ["sku"]}},
        {"name": "check_multiple_inventory",
         "description": "Check stock availability for multiple SKUs in one call",
         "inputSchema": {"type": "object",
                         "properties": {"skus": {"type": "array", "items": {"type": "string"}}},
                         "required": ["skus"]}},
    ])
    print(f"  inventory_check_tool ready : {inventory_id}")

    shipping_id = _create_target(agentcore_client, gateway_id,
        f"shipping-track-{timestamp}", lambda_arn, [
        {"name": "track_shipment",
         "description": "Track a shipment by shipment_id or order_id",
         "inputSchema": {"type": "object",
                         "properties": {
                             "shipment_id": {"type": "string"},
                             "order_id":    {"type": "string"}
                         }}},
        {"name": "estimate_delivery",
         "description": "Get estimated delivery date and carrier info for an order",
         "inputSchema": {"type": "object",
                         "properties": {"order_id": {"type": "string"}},
                         "required": ["order_id"]}},
    ])
    print(f"  shipping_track_tool  ready : {shipping_id}")

    return {
        "lambda_fn_name": fn_name,
        "lambda_arn":     lambda_arn,
        "targets": {
            "payment_status":  payment_id,
            "inventory_check": inventory_id,
            "shipping_track":  shipping_id,
        },
    }
