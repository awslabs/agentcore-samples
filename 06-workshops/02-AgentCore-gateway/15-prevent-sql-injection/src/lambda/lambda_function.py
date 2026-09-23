"""
SQL Injection Prevention Interceptor (Gateway REQUEST Interceptor)

Purpose
- Intercepts MCP tools/call requests and evaluates tool arguments before they reach
  backend database tools.
- Provides deterministic, tool-level enforcement to prevent unsafe SQL execution.
- Fails closed: blocks requests when suspicious patterns are detected or when validation
  and analysis fail.

Scope
- Operates on tool arguments (tool inputs), not on the original user prompt.
- Executes at the tool boundary, before any database interaction occurs.
- Intended to prevent the agent or caller from passing raw or unsafe SQL content to
  database-facing tools.

Extensibility
Because this control is implemented as AWS Lambda, you can integrate:
- Schema and contract validation libraries
- Policy engines and authorization checks (tenant, role, action allow lists)
- Internal security services and compliance logic
- Third-party paid security services via SDKs or API calls (e.g., risk scoring, DLP,
  threat intelligence, API security platforms)
- Centralized logging and monitoring systems for audit and incident response

Production Considerations
This implementation uses heuristic pattern detection to identify common SQL injection
techniques. For production systems, avoid accepting raw SQL from agents and prefer:
- Structured tool contracts (query templates or JSON intent with typed parameters)
- Allow-listed operations, tables, and fields
- Strict schema validation and bounds checking
- Tenant isolation and least-privilege access
- Parameterized queries / prepared statements in the database layer
"""

import re
import hashlib
from typing import Any, Dict, Tuple, List

STRICT_MODE = False
MAX_STRING_LENGTH = 10000

SQL_INJECTION_PATTERNS = [
    (
        r";[\s\n]*\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b",
        "STACKED_QUERY",
    ),
    (r"\b(DROP|TRUNCATE)\b[\s\n]+\b(TABLE|DATABASE|SCHEMA)\b", "DANGEROUS_DDL"),
    (r"--", "SQL_COMMENT_DASH"),
    (r"/\*", "SQL_COMMENT_OPEN"),
    (r"\*/", "SQL_COMMENT_CLOSE"),
    (r"\bUNION\b[\s\n]+\bSELECT\b", "UNION_SELECT"),
    (r"\bUNION\b[\s\n]+\bALL\b[\s\n]+\bSELECT\b", "UNION_ALL_SELECT"),
    (r"\bOR\b[\s\n]+1[\s\n]*=[\s\n]*1", "TAUTOLOGY_OR"),
    (r"\bAND\b[\s\n]+1[\s\n]*=[\s\n]*1", "TAUTOLOGY_AND"),
    (r"\bSLEEP\b[\s\n]*\(", "TIME_SLEEP"),
    (r"\bWAITFOR\b[\s\n]+\bDELAY\b", "TIME_WAITFOR"),
    (r"\bBENCHMARK\b[\s\n]*\(", "TIME_BENCHMARK"),
    (r"\b(EXEC|EXECUTE|sp_executesql)\b", "DYNAMIC_SQL"),
    (
        r"\b(ALTER|RENAME|GRANT|REVOKE)\b[\s\n]+\b(TABLE|DATABASE|USER)\b",
        "DANGEROUS_DDL_EXTENDED",
    ),
    (r"\bCONCAT\b[\s\n]*\(", "STRING_CONCAT"),
    (r"\bCHR\b[\s\n]*\(|\bCHAR\b[\s\n]*\(", "CHAR_ENCODING"),
    (r"\bSUBSTRING\b[\s\n]*\(|\bSUBSTR\b[\s\n]*\(", "SUBSTRING_PROBE"),
    (r"\bCONVERT\b[\s\n]*\(|\bCAST\b[\s\n]*\(", "TYPE_CONVERSION"),
    (r"0x[0-9a-fA-F]+", "HEX_ENCODING"),
    (r"\bINFORMATION_SCHEMA\b", "SCHEMA_ENUMERATION"),
    (r"\bLOAD_FILE\b|\bINTO\b[\s\n]+\bOUTFILE\b", "FILE_OPERATIONS"),
]

COMPILED_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), rule_id) for pattern, rule_id in SQL_INJECTION_PATTERNS
]


def normalize_string(s: str) -> str:
    normalized = re.sub(r"\s+", " ", s)
    return normalized.lower()


def compute_query_hash(query: str) -> str:
    return hashlib.sha256(query.encode()).hexdigest()[:16]


def extract_all_strings(obj: Any, path: str = "") -> List[Tuple[str, str]]:
    strings = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{path}.{key}" if path else key
            strings.extend(extract_all_strings(value, new_path))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            new_path = f"{path}[{idx}]"
            strings.extend(extract_all_strings(item, new_path))
    elif isinstance(obj, str):
        strings.append((path, obj))

    return strings


def detect_sql_injection(value: str, field_path: str = "") -> Tuple[bool, str, str]:
    if not value:
        return False, "", ""

    if len(value) > MAX_STRING_LENGTH:
        return True, "STRING_TOO_LONG", "INVALID_INPUT"

    normalized = normalize_string(value)

    for pattern, rule_id in COMPILED_PATTERNS:
        if pattern.search(normalized):
            return True, rule_id, "SQL_INJECTION_DETECTED"

    return False, "", ""


def analyze_arguments_for_sql_injection(
    arguments: Dict[str, Any],
) -> Tuple[bool, str, str]:
    all_strings = extract_all_strings(arguments)

    if not all_strings:
        return True, "", ""

    for field_path, value in all_strings:
        is_malicious, rule_id, category = detect_sql_injection(value, field_path)

        if is_malicious:
            value_hash = compute_query_hash(value)
            print(f"[SECURITY] SQL injection detected | field={field_path} | rule={rule_id} | hash={value_hash}")
            return False, rule_id, category

    return True, "", ""


def create_blocked_response(category: str, request_id: Any) -> Dict[str, Any]:
    generic_message = "Request blocked by security policy"

    blocked_response = {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 403,
                "headers": {
                    "Content-Type": "application/json",
                    "X-Security-Status": "BLOCKED",
                },
                "body": {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32000,
                        "message": generic_message,
                        "data": {
                            "category": category,
                            "security_policy": "sql_injection_prevention",
                        },
                    },
                },
            }
        },
    }

    return blocked_response


def lambda_handler(event, context):
    try:
        mcp_data = event.get("mcp", {})
        gateway_request = mcp_data.get("gatewayRequest", {})
        request_body = gateway_request.get("body", {})

        method = request_body.get("method", "")
        request_id = request_body.get("id", "unknown")

        print(f"[INFO] Interceptor invoked | request_id={request_id} | method={method}")

        if method != "tools/call":
            print(f"[INFO] Method not tools/call, passing through | request_id={request_id}")
            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayRequest": {
                        "headers": gateway_request.get("headers", {}),
                        "body": request_body,
                    }
                },
            }

        params = request_body.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        print(f"[INFO] Analyzing tool call | request_id={request_id} | tool={tool_name}")

        if STRICT_MODE:
            if "query" in arguments or "sql" in arguments:
                print(f"[SECURITY] STRICT MODE: Raw SQL field rejected | request_id={request_id} | tool={tool_name}")
                return create_blocked_response("RAW_SQL_NOT_ALLOWED", request_id)

        is_safe, rule_id, category = analyze_arguments_for_sql_injection(arguments)

        if is_safe:
            print(f"[INFO] Request allowed | request_id={request_id} | tool={tool_name}")

            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayRequest": {
                        "headers": gateway_request.get("headers", {}),
                        "body": request_body,
                    }
                },
            }
        else:
            print(f"[SECURITY] Request blocked | request_id={request_id} | tool={tool_name} | rule={rule_id}")
            return create_blocked_response(category, request_id)

    except Exception as e:
        print(f"[ERROR] Interceptor error | request_id={request_body.get('id', 'unknown')} | error={str(e)[:100]}")
        return create_blocked_response("INTERCEPTOR_ERROR", request_body.get("id", "unknown"))
