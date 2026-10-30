import json
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

MEMORY_ID_SSM_PARAM = os.environ.get("MEMORY_ID_SSM_PARAM", "/insurance-claims-demo/memory_id")
DECISION_MODE_SSM_PARAM = os.environ.get("DECISION_MODE_SSM_PARAM", "/insurance-claims-demo/decision_mode")
REFLECTION_NAMESPACE = "claims/"

_ssm = boto3.client("ssm")
_bac = boto3.client("bedrock-agentcore")


def _get_ssm(param, default=""):
    try:
        return _ssm.get_parameter(Name=param)["Parameter"]["Value"]
    except (ClientError, BotoCoreError):
        return default


MEMORY_ID = _get_ssm(MEMORY_ID_SSM_PARAM)


def _parse_event(e):
    out = {
        "eventId": e.get("eventId"),
        "timestamp": str(e.get("eventTimestamp")),
        "created_at": None,
        "branch": e.get("branch"),
        "kind": "other",
        "role": None,
        "tool": None,
        "text": "",
        "metadata": {},
    }
    for p in e.get("payload", []) or []:
        if "blob" in p:
            out["kind"] = "state"
            out["role"] = "system"
            out["text"] = "Short-term memory snapshot."
            continue

        conv = p.get("conversational")
        if not conv:
            continue
        out["role"] = (conv.get("role") or "").lower()
        raw = (conv.get("content") or {}).get("text", "")
        try:
            j = json.loads(raw)
            msg = j.get("message", {})
            out["role"] = (msg.get("role") or out["role"] or "").lower()
            out["created_at"] = j.get("created_at")
            out["metadata"] = msg.get("metadata", {})
            parts = []
            kind = "message"
            for c in msg.get("content", []) or []:
                if "text" in c:
                    parts.append(c["text"])
                elif "toolUse" in c:
                    tu = c["toolUse"]
                    kind = "tool_use"
                    out["tool"] = tu.get("name")
                    parts.append(f"Tool: {tu.get('name')}({json.dumps(tu.get('input', {}))})")
                elif "toolResult" in c:
                    tr = c["toolResult"]
                    kind = "tool_result"
                    out["tool"] = tr.get("toolUseId")
                    res = []
                    for rc in tr.get("content", []) or []:
                        if "text" in rc:
                            res.append(rc["text"])
                        elif "json" in rc:
                            res.append(json.dumps(rc["json"]))
                    parts.append("↩ " + ("\n".join(res) if res else f"[{tr.get('status', 'result')}]"))
            out["kind"] = kind
            out["text"] = "\n".join(parts)
        except (ValueError, TypeError):
            out["kind"] = "message"
            out["text"] = raw
            if raw.startswith("[ADJUSTER DECISION]"):
                out["role"] = "adjuster"
    return out


def _parse_record(r):
    text = (r.get("content") or {}).get("text", "")
    parsed = None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        pass
    meta = r.get("metadata") or {}
    flat_meta = {}
    for k, v in meta.items():
        if isinstance(v, dict):
            flat_meta[k] = v.get("stringValue") or v.get("numberValue") or str(v.get("dateTimeValue", ""))
        else:
            flat_meta[k] = v
    return {
        "recordId": r.get("memoryRecordId"),
        "createdAt": str(r.get("createdAt")),
        "namespaces": r.get("namespaces"),
        "strategyId": r.get("memoryStrategyId"),
        "text": text,
        "parsed": parsed,
        "metadata": flat_meta,
    }


def _list_events(memory_id, actor_id, session_id):
    out, token = [], None
    while True:
        kw = {
            "memoryId": memory_id,
            "actorId": actor_id,
            "sessionId": session_id,
            "maxResults": 100,
            "includePayloads": True,
        }
        if token:
            kw["nextToken"] = token
        r = _bac.list_events(**kw)
        out.extend(r.get("events", []))
        token = r.get("nextToken")
        if not token:
            break
    return out


def _list_records(namespace):
    out, token = [], None
    while True:
        kw = {"memoryId": MEMORY_ID, "namespace": namespace, "maxResults": 100}
        if token:
            kw["nextToken"] = token
        r = _bac.list_memory_records(**kw)
        out.extend(r.get("memoryRecordSummaries", []))
        token = r.get("nextToken")
        if not token:
            break
    return out


def handler(event, context):
    try:
        params = event.get("queryStringParameters") or {}
        actor_id = params.get("actorId", "")
        session_id = params.get("sessionId", "")

        memory_id = MEMORY_ID or _get_ssm(MEMORY_ID_SSM_PARAM)
        decision_mode = _get_ssm(DECISION_MODE_SSM_PARAM, "auto")

        out = {
            "memory_id": memory_id,
            "decision_mode": decision_mode,
            "events": [],
            "subtools": [],
            "episodes": [],
            "reflections": [],
        }

        # Raw events for actor+session
        if actor_id and session_id:
            try:
                evs = _list_events(memory_id, actor_id, session_id)
                out["events"] = [_parse_event(e) for e in reversed(evs)]
            except Exception as e:  # noqa: BLE001 - best-effort: return partial data if a section fails
                print(f"list_events failed: {e}")

            # Subtool trace events (written by memory tools for observability)
            try:
                sub_evs = _list_events(memory_id, "system", session_id)
                for e in reversed(sub_evs):
                    for p in e.get("payload", []) or []:
                        conv = p.get("conversational")
                        if not conv:
                            continue
                        raw = (conv.get("content") or {}).get("text", "")
                        try:
                            parsed = json.loads(raw)
                            out["subtools"].append(
                                {
                                    "eventId": e.get("eventId"),
                                    "timestamp": str(e.get("eventTimestamp")),
                                    "tool": parsed.get("tool", ""),
                                    "query": parsed.get("query", ""),
                                    "filter": parsed.get("filter", ""),
                                    "result_count": parsed.get("result_count", 0),
                                    "results": parsed.get("results", []),
                                }
                            )
                        except (ValueError, TypeError):
                            pass
            except Exception as e:  # noqa: BLE001 - best-effort: return partial data if a section fails
                print(f"list subtool events failed: {e}")

        # Episodes
        if actor_id:
            ns = f"claims/{actor_id}/{session_id}/" if session_id else f"claims/{actor_id}/"
            try:
                recs = _list_records(ns)
                out["episodes"] = [
                    _parse_record(r)
                    for r in recs
                    if any(n.startswith(f"claims/{actor_id}/") for n in (r.get("namespaces") or []))
                ]
            except Exception as e:  # noqa: BLE001 - best-effort: return partial data if a section fails
                print(f"list episodes failed: {e}")

        # Reflections
        try:
            recs = _list_records(REFLECTION_NAMESPACE)
            out["reflections"] = [_parse_record(r) for r in recs if REFLECTION_NAMESPACE in (r.get("namespaces") or [])]
        except Exception as e:  # noqa: BLE001 - best-effort: return partial data if a section fails
            print(f"list reflections failed: {e}")

        out["counts"] = {k: len(out[k]) for k in ("events", "subtools", "episodes", "reflections")}

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            },
            "body": json.dumps(out, default=str),
        }

    except Exception as e:  # noqa: BLE001 - handler boundary
        print(f"Error: {e}")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            },
            "body": json.dumps({"error": "Internal server error"}),
        }
