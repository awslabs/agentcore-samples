import os
import time

import boto3
from botocore.config import Config

TABLE_NAME = os.environ["TABLE_NAME"]
AGENTS_TABLE_NAME = os.environ["AGENTS_TABLE_NAME"]
DEFAULT_ENDPOINT = "DEFAULT"

_cfg = Config(retries={"mode": "adaptive", "max_attempts": 5},
              max_pool_connections=16, read_timeout=120)
table = boto3.resource("dynamodb").Table(TABLE_NAME)
agents_table = boto3.resource("dynamodb").Table(AGENTS_TABLE_NAME)
agentcore = boto3.client("bedrock-agentcore", config=_cfg)

# Single-table design on AgentsTable: PK/SK are generic so the table can grow new entity types
# later without a schema migration.
#   PK="AGENT#<arn>"  SK="ENDPOINT#<endpoint>"  -> one pool: minPoolSize, optional per-pool
#                                                  overrides (retireAfterS, maxLifetimeS, claimCandidates)
#   PK="GLOBAL"       SK="CONFIG"               -> shared settings, and the defaults a pool
#                                                  inherits when it doesn't override them
# No Lambda env var controls pool behavior anymore except where to find these two tables and the
# sibling function to invoke - everything else is read here, cached briefly, so a change takes
# effect on the next sweep (seconds) instead of a redeploy (~1 min).
GLOBAL_KEY = {"PK": "GLOBAL", "SK": "CONFIG"}
# Fallback when GLOBAL/CONFIG itself doesn't set a key, or doesn't exist yet (fresh stack).
DEFAULTS = {"refillConcurrency": 8, "maxAddsPerRun": 20, "retireAfterS": 14400,
           "maxLifetimeS": 28800, "claimCandidates": 20}

_CACHE_S = 60
_cache = {}   # cache key -> (expires_at, value)


def _cached(key, fetch):
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = fetch()
    _cache[key] = (now + _CACHE_S, value)
    return value


def agent_key(arn, endpoint):
    return {"PK": f"AGENT#{arn}", "SK": f"ENDPOINT#{endpoint}"}


def pool_key(arn, endpoint):
    """Partition key of PoolTable's sparse index: one pool per (arn, endpoint)."""
    return f"{arn}#{endpoint}"


def get_global_config():
    """GLOBAL/CONFIG merged over DEFAULTS. Cached - see module docstring."""
    def fetch():
        item = agents_table.get_item(Key=GLOBAL_KEY).get("Item", {})
        return {**DEFAULTS, **{k: int(v) for k, v in item.items() if k in DEFAULTS}}
    return _cached("global", fetch)


def get_pool(arn, endpoint):
    """Resolved settings for one (arn, endpoint), or None if it isn't registered: per-pool
    overrides falling back to GLOBAL/CONFIG falling back to DEFAULTS.
    """
    def fetch():
        return agents_table.get_item(Key=agent_key(arn, endpoint)).get("Item")
    item = _cached(("pool", arn, endpoint), fetch)
    if item is None:
        return None
    cfg = get_global_config()
    return {
        "minPoolSize": int(item["minPoolSize"]),
        "retireAfterS": int(item.get("retireAfterS", cfg["retireAfterS"])),
        "maxLifetimeS": int(item.get("maxLifetimeS", cfg["maxLifetimeS"])),
        "claimCandidates": int(item.get("claimCandidates", cfg["claimCandidates"])),
    }


def agent_pools():
    """Every registered pool, settings already resolved. Small table - a full scan is fine."""
    cfg = get_global_config()
    items, kwargs = [], {}
    while True:
        resp = agents_table.scan(**kwargs)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    pools = []
    for it in items:
        if not it["SK"].startswith("ENDPOINT#"):
            continue   # the GLOBAL/CONFIG row (or a future entity type)
        pools.append({
            "arn": it["PK"][len("AGENT#"):],
            "endpoint": it["SK"][len("ENDPOINT#"):],
            "minPoolSize": int(it["minPoolSize"]),
            "retireAfterS": int(it.get("retireAfterS", cfg["retireAfterS"])),
            "maxLifetimeS": int(it.get("maxLifetimeS", cfg["maxLifetimeS"])),
        })
    return pools


def stop_session(session_id, endpoint, arn):
    """Stop a session on its endpoint. A session that already ended is not an error."""
    try:
        agentcore.stop_runtime_session(agentRuntimeArn=arn, runtimeSessionId=session_id,
                                       qualifier=endpoint)
    except agentcore.exceptions.ResourceNotFoundException:
        pass
