"""Document MCP server - an AgentCore Gateway Lambda target.

The gateway speaks MCP on our behalf. This Lambda just receives the tool
arguments as ``event`` and reads the invoked tool name from the Lambda client
context (``bedrockAgentCoreToolName``), which arrives prefixed as
``<target_name>___<tool_name>``.

Tools:
  - GetDocument(documentId)   -> a single mock document
  - ListDocuments(category?)  -> list of document summaries
  - SearchDocuments(query)    -> keyword search over the mock corpus
"""

import json

TOOL_NAME_DELIMITER = "___"

# Mock document corpus for the fictional "AnyCompany" product catalog.
DOCUMENTS = {
    "whitepaper-123": {
        "documentId": "whitepaper-123",
        "title": "AnyCompany Secure Edge Processing Whitepaper",
        "category": "whitepaper",
        "body": "An overview of AnyCompany's secure edge processing architecture "
        "for industrial and connected-device workloads.",
    },
    "datasheet-edge-sensor": {
        "documentId": "datasheet-edge-sensor",
        "title": "Edge Sensor Applications Processor Datasheet",
        "category": "datasheet",
        "body": "Electrical characteristics, pinout, and power specifications for "
        "the Edge Sensor family.",
    },
    "appnote-gateway-router": {
        "documentId": "appnote-gateway-router",
        "title": "Gateway Router Application Note",
        "category": "application-note",
        "body": "Design guidance for integrating AnyCompany gateway routers in "
        "connected-device networks.",
    },
}


def _get_document(args):
    doc_id = args.get("documentId")
    if not doc_id:
        raise ValueError("documentId is required")
    doc = DOCUMENTS.get(doc_id)
    if not doc:
        raise ValueError(f"Document not found: {doc_id}")
    return doc


def _list_documents(args):
    category = args.get("category")
    docs = DOCUMENTS.values()
    if category:
        docs = [d for d in docs if d["category"] == category]
    return {
        "documents": [
            {
                "documentId": d["documentId"],
                "title": d["title"],
                "category": d["category"],
            }
            for d in docs
        ]
    }


def _search_documents(args):
    query = (args.get("query") or "").lower().strip()
    if not query:
        raise ValueError("query is required")
    hits = [
        {"documentId": d["documentId"], "title": d["title"]}
        for d in DOCUMENTS.values()
        if query in d["title"].lower() or query in d["body"].lower()
    ]
    return {"query": query, "results": hits, "count": len(hits)}


TOOLS = {
    "GetDocument": _get_document,
    "ListDocuments": _list_documents,
    "SearchDocuments": _search_documents,
}


def _resolve_tool_name(context):
    """Read the tool name from the gateway-provided client context and strip
    the ``<target>___`` prefix."""
    custom = getattr(context, "client_context", None)
    raw = None
    if custom is not None and getattr(custom, "custom", None):
        raw = custom.custom.get("bedrockAgentCoreToolName")
    if not raw:
        return None
    if TOOL_NAME_DELIMITER in raw:
        return raw.split(TOOL_NAME_DELIMITER, 1)[1]
    return raw


def lambda_handler(event, context):
    tool_name = _resolve_tool_name(context)
    print(json.dumps({"tool": tool_name, "args": event}))

    handler = TOOLS.get(tool_name)
    if handler is None:
        return {
            "statusCode": 400,
            "error": f"Unknown tool: {tool_name}",
            "availableTools": list(TOOLS.keys()),
        }

    try:
        return handler(event or {})
    except ValueError as exc:
        return {"statusCode": 400, "error": str(exc)}
