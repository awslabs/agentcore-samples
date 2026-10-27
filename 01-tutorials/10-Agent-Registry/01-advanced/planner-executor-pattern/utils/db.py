"""
DynamoDB helper bundled into every Lambda zip and A2A agent.
Table names are injected via environment variables (ORDERS_TABLE, etc.).
"""
import boto3
import os
import json
from decimal import Decimal
from boto3.dynamodb.conditions import Attr, Key

_ddb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))

def _table(env_key: str):
    return _ddb.Table(os.environ[env_key])

def _from_ddb(obj):
    """Decimal → float recursively (DynamoDB stores numbers as Decimal)."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _from_ddb(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_ddb(i) for i in obj]
    return obj

def _to_ddb(obj):
    """float → Decimal recursively (required for DynamoDB puts/updates)."""
    return json.loads(json.dumps(obj), parse_float=Decimal)

# ── Read helpers ───────────────────────────────────────────────────────────

def get_item(env_key: str, key: dict):
    """Fetch a single item by primary key. Returns None if not found."""
    resp = _table(env_key).get_item(Key=key)
    return _from_ddb(resp.get("Item"))

def scan_all(env_key: str) -> list:
    """Full table scan — fine for small demo tables."""
    return _from_ddb(_table(env_key).scan().get("Items", []))

def scan_filter(env_key: str, attr: str, val) -> list:
    """Scan with a simple equality filter on any attribute."""
    resp = _table(env_key).scan(FilterExpression=Attr(attr).eq(val))
    return _from_ddb(resp.get("Items", []))

def query_gsi(env_key: str, index: str, key_attr: str, key_val: str) -> list:
    """Query a GSI (e.g. order_id-index on payments/shipments)."""
    resp = _table(env_key).query(
        IndexName=index,
        KeyConditionExpression=Key(key_attr).eq(key_val),
    )
    return _from_ddb(resp.get("Items", []))

# ── Write helpers ──────────────────────────────────────────────────────────

def put_item(env_key: str, item: dict):
    """Insert or replace an item."""
    _table(env_key).put_item(Item=_to_ddb(item))

def update_attrs(env_key: str, key: dict, attrs: dict):
    """Update specific attributes on an existing item."""
    expr   = "SET " + ", ".join(f"#a{i}=:v{i}" for i in range(len(attrs)))
    names  = {f"#a{i}": k for i, k in enumerate(attrs)}
    values = {f":v{i}": _to_ddb(v) for i, v in enumerate(attrs.values())}
    _table(env_key).update_item(
        Key=key,
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )