#!/usr/bin/env python3
"""Manage the AgentsTable registry: pools, and the shared settings every pool falls back to.

  register --arn ARN --endpoint NAME --min-pool-size N   add or update a pool
           [--retire-after-s N] [--max-lifetime-s N] [--claim-candidates N]
  remove --arn ARN --endpoint NAME                        stop managing a pool
         (sessions already in it are left alone; drain it first if you want them stopped)
  list                                                     every registered pool
  configure [--refill-concurrency N] [--max-adds-per-run N]
            [--retire-after-s N] [--max-lifetime-s N] [--claim-candidates N]
    with no flags: print the current GLOBAL/CONFIG row (and the hardcoded fallback for
    anything it doesn't set). With flags: update only the ones given.

Nothing here is a Lambda env var: the pool manager re-reads this table every sweep, and the
broker caches each lookup for ~60s, so a change here takes effect in seconds, not a redeploy.
Per-pool --retire-after-s/--max-lifetime-s/--claim-candidates override configure's defaults for
that one pool; omit them to inherit whatever configure (or the hardcoded fallback) says.
"""
import argparse

import boto3

GLOBAL_KEY = {"PK": "GLOBAL", "SK": "CONFIG"}
DEFAULTS = {"refillConcurrency": 8, "maxAddsPerRun": 20, "retireAfterS": 14400,
           "maxLifetimeS": 28800, "claimCandidates": 20}


def agent_key(arn, endpoint):
    return {"PK": f"AGENT#{arn}", "SK": f"ENDPOINT#{endpoint}"}


def add_overrides(item, a, names):
    for flag, attr in names.items():
        value = getattr(a, flag, None)
        if value is not None:
            item[attr] = value


ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--table", required=True)
sub = ap.add_subparsers(dest="cmd", required=True)

OVERRIDE_FLAGS = {"retire_after_s": "retireAfterS", "max_lifetime_s": "maxLifetimeS",
                  "claim_candidates": "claimCandidates"}

reg = sub.add_parser("register")
reg.add_argument("--arn", required=True)
reg.add_argument("--endpoint", required=True)
reg.add_argument("--min-pool-size", type=int, required=True)
reg.add_argument("--retire-after-s", type=int)
reg.add_argument("--max-lifetime-s", type=int)
reg.add_argument("--claim-candidates", type=int)

rm = sub.add_parser("remove")
rm.add_argument("--arn", required=True)
rm.add_argument("--endpoint", required=True)

sub.add_parser("list")

cfg = sub.add_parser("configure")
cfg.add_argument("--refill-concurrency", type=int)
cfg.add_argument("--max-adds-per-run", type=int)
cfg.add_argument("--retire-after-s", type=int)
cfg.add_argument("--max-lifetime-s", type=int)
cfg.add_argument("--claim-candidates", type=int)

a = ap.parse_args()
table = boto3.resource("dynamodb").Table(a.table)

if a.cmd == "register":
    item = {**agent_key(a.arn, a.endpoint), "arn": a.arn, "endpoint": a.endpoint,
            "minPoolSize": a.min_pool_size}
    add_overrides(item, a, OVERRIDE_FLAGS)
    table.put_item(Item=item)
    print(f"registered {a.arn} / {a.endpoint} (minPoolSize={a.min_pool_size})")

elif a.cmd == "remove":
    table.delete_item(Key=agent_key(a.arn, a.endpoint))
    print(f"removed {a.arn} / {a.endpoint} from the registry")

elif a.cmd == "list":
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    pools = [it for it in items if it["SK"].startswith("ENDPOINT#")]
    print(f"{'arn':60} {'endpoint':16} {'minPoolSize':>11}  overrides")
    for it in sorted(pools, key=lambda i: (i["arn"], i["endpoint"])):
        overrides = ", ".join(f"{attr}={it[attr]}" for attr in OVERRIDE_FLAGS.values() if attr in it)
        print(f"{it['arn']:60} {it['endpoint']:16} {it['minPoolSize']:>11}  {overrides or '-'}")

elif a.cmd == "configure":
    flags = {"refill_concurrency": "refillConcurrency", "max_adds_per_run": "maxAddsPerRun",
             **OVERRIDE_FLAGS}
    updates = {attr: getattr(a, flag) for flag, attr in flags.items() if getattr(a, flag) is not None}
    if updates:
        expr = ", ".join(f"#{k} = :{k}" for k in updates)
        table.update_item(Key=GLOBAL_KEY, UpdateExpression=f"SET {expr}",
                          ExpressionAttributeNames={f"#{k}": k for k in updates},
                          ExpressionAttributeValues={f":{k}": v for k, v in updates.items()})
        print(f"updated: {', '.join(f'{k}={v}' for k, v in updates.items())}")
    item = table.get_item(Key=GLOBAL_KEY).get("Item", {})
    print("current GLOBAL/CONFIG:")
    for k, default in DEFAULTS.items():
        if k in item:
            print(f"  {k}: {item[k]}")
        else:
            print(f"  {k}: {default}  (hardcoded fallback - not set yet)")
