"""
SQL Injection Prevention Interceptor for Gateway MCP REQUESTS

Intercepts tools/call requests and analyzes tool arguments for SQL injection patterns
before they reach database tools. Fails closed on detection or errors.

NOTE: This is a demo/testing implementation showing pattern-based SQL injection detection.
For production, implement your own security logic in this Lambda - you can integrate with
any security service, validation library, or custom business logic as needed.

"""

import json
import re
import hashlib
from typing import Any, Dict, Tuple, List

STRICT_MODE = False
MAX_STRING_LENGTH = 10000

SQL_INJECTION_PATTERNS = [
    (r';[\s\n]*\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b', 'STACKED_QUERY'),
    (r'--', 'SQL_COMMENT_DASH'),
    (r'/\*', 'SQL_COMMENT_OPEN'),
    (r'\*/', 'SQL_COMMENT_CLOSE'),
    (r'\bUNION\b[\s\n]+\bSELECT\b', 'UNION_SELECT'),
    (r'\bUNION\b[\s\n]+\bALL\b[\s\n]+\bSELECT\b', 'UNION_ALL_SELECT'),
    (r'\bOR\b[\s\n]+1[\s\n]*=[\s\n]*1', 'TAUTOLOGY_OR'),
    (r'\bAND\b[\s\n]+1[\s\n]*=[\s\n]*1', 'TAUTOLOGY_AND'),
    (r'\bSLEEP\b[\s\n]*\(', 'TIME_SLEEP'),
    (r'\bWAITFOR\b[\s\n]+\bDELAY\b', 'TIME_WAITFOR'),
    (r'\bBENCHMARK\b[\s\n]*\(', 'TIME_BENCHMARK'),
]

COMPILED_PATTERNS = [(re.compile(pattern, re.IGNORECASE | re.MULTILINE), rule_id) 
                     for pattern, rule_id in SQL_INJECTION_PATTERNS]


def normalize_string(s: str) -> str:
    normalized = re.sub(r'\s+', ' ', s)
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


def analyze_arguments_for_sql_injection(arguments: Dict[str, Any]) -> Tuple[bool, str, str]:
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
                    "X-Security-Status": "BLOCKED"
                },
                "body": {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32000,
                        "message": generic_message,
                        "data": {
                            "category": category,
                            "security_policy": "sql_injection_prevention"
                        }
                    }
                }
            }
        }
    }
    
    return blocked_response


def lambda_handler(event, context):
    try:
        mcp_data = event.get('mcp', {})
        gateway_request = mcp_data.get('gatewayRequest', {})
        request_body = gateway_request.get('body', {})
        
        method = request_body.get('method', '')
        request_id = request_body.get('id', 'unknown')
        
        print(f"[INFO] Interceptor invoked | request_id={request_id} | method={method}")
        
        if method != 'tools/call':
            print(f"[INFO] Method not tools/call, passing through | request_id={request_id}")
            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayRequest": {
                        "headers": gateway_request.get('headers', {}),
                        "body": request_body
                    }
                }
            }
        
        params = request_body.get('params', {})
        tool_name = params.get('name', '')
        arguments = params.get('arguments', {})
        
        print(f"[INFO] Analyzing tool call | request_id={request_id} | tool={tool_name}")
        
        if STRICT_MODE:
            if 'query' in arguments or 'sql' in arguments:
                print(f"[SECURITY] STRICT MODE: Raw SQL field rejected | request_id={request_id} | tool={tool_name}")
                return create_blocked_response("RAW_SQL_NOT_ALLOWED", request_id)
        
        is_safe, rule_id, category = analyze_arguments_for_sql_injection(arguments)
        
        if is_safe:
            print(f"[INFO] Request allowed | request_id={request_id} | tool={tool_name}")
            
            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayRequest": {
                        "headers": gateway_request.get('headers', {}),
                        "body": request_body
                    }
                }
            }
        else:
            print(f"[SECURITY] Request blocked | request_id={request_id} | tool={tool_name} | rule={rule_id}")
            return create_blocked_response(category, request_id)
    
    except Exception as e:
        print(f"[ERROR] Interceptor error | request_id={request_body.get('id', 'unknown')} | error={str(e)[:100]}")
        return create_blocked_response("INTERCEPTOR_ERROR", request_body.get('id', 'unknown'))
