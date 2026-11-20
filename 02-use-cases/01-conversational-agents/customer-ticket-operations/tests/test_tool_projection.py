"""Installed MCPAgentTool projection of the sanitized actual Gateway tools/list schema."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from mcp.types import Tool
from strands.tools.mcp.mcp_agent_tool import MCPAgentTool
from strands.tools.mcp.mcp_client import PaginatedList

from ticket_agent.agent import ARGUMENT_FIELDS
from ticket_agent.tool_projection import MODEL_TOOLS, project_gateway_tools

CAPTURE = json.loads((Path(__file__).parent / "fixtures/gateway_tools_v3.json").read_text())
ADDITIONAL_CASES = [
    (scope, label, bad)
    for scope in ("root", "actor")
    for label, bad in (
        ("zero_int", 0),
        ("zero_float", 0.0),
        ("null", None),
        ("true", True),
        ("string", "false"),
        ("array", []),
        ("object", {}),
    )
]
ADDITIONAL_IDS = [f"{scope}-{label}" for scope, label, _ in ADDITIONAL_CASES]


def additional_schema(rows, scope):
    raw = next(row for row in rows if row["name"] == "TicketTools___list_tickets")["inputSchema"]
    return raw if scope == "root" else raw["properties"]["actor"]


class Gateway:
    def __init__(self, rows=None):
        self.tools = [MCPAgentTool(Tool(**row), self) for row in (rows or CAPTURE["tools"])]
        self.starts, self.lists = 0, 0

    def start(self):
        self.starts += 1

    def list_tools_sync(self, pagination_token=None):
        assert pagination_token is None
        self.lists += 1
        return PaginatedList(self.tools)


class PageGateway(Gateway):
    def __init__(self, pages):
        super().__init__()
        self.pages, self.tokens = pages, []

    def list_tools_sync(self, pagination_token=None):
        self.tokens.append(pagination_token)
        self.lists += 1
        expected, indices, next_token = self.pages[self.lists - 1]
        assert pagination_token == expected
        if isinstance(indices, Exception):
            raise indices
        if not isinstance(indices, list):
            return indices  # malformed SDK page
        return PaginatedList([self.tools[i] for i in indices], next_token)


def test_two_page_projection_preserves_raw_schema_and_same_gateway_client():
    gateway = PageGateway([(None, [0, 1], "page-2"), ("page-2", [2, 3], None)])
    raw = copy.deepcopy([tool.mcp_tool.model_dump(by_alias=True) for tool in gateway.tools])
    projected = project_gateway_tools(gateway, ARGUMENT_FIELDS)
    assert gateway.starts == 1 and gateway.tokens == [None, "page-2"]
    assert len(projected) == 3 and all(tool.mcp_client is gateway for tool in projected)
    assert raw == [tool.mcp_tool.model_dump(by_alias=True) for tool in gateway.tools]
    assert all(
        tool.mcp_tool
        is not next(raw for raw in gateway.tools if raw.tool_name == tool.tool_name).mcp_tool
        for tool in projected
    )


@pytest.mark.parametrize(
    "pages,expected_calls",
    [
        ([(None, [0, 1], "page-2"), ("page-2", [2, 3], "page-2")], 2),
        ([(None, [0, 1], "")], 1),
        ([(None, [0, 1], 0)], 1),
        ([(None, [0, 1], "page-2"), ("page-2", [0, 1], None)], 2),
        ([(None, "invalid page", None)], 1),
        (
            [
                (None, [0], "page-2"),
                ("page-2", [1], "page-3"),
                ("page-3", [2], "page-4"),
                ("page-4", [3], "page-5"),
            ],
            4,
        ),
    ],
    ids=(
        "repeated_cursor",
        "empty_cursor",
        "wrong_type_cursor",
        "ignored_cursor",
        "malformed_page",
        "page_bound",
    ),
)
def test_untrusted_pagination_fails_closed_without_model(pages, expected_calls):
    gateway = PageGateway(pages)
    with pytest.raises(ValueError, match="Gateway|tool"):
        project_gateway_tools(gateway, ARGUMENT_FIELDS)
    assert gateway.starts == 1 and gateway.lists == expected_calls


def test_gateway_tools_list_exception_is_not_interpreted_as_empty_schema():
    gateway = PageGateway([(None, ConnectionError("read failed"), None)])
    with pytest.raises(ConnectionError, match="read failed"):
        project_gateway_tools(gateway, ARGUMENT_FIELDS)
    assert gateway.starts == gateway.lists == 1


def test_actual_captured_gateway_schema_projects_only_model_owned_fields():
    gateway = Gateway()
    raw = copy.deepcopy([tool.mcp_tool.model_dump(by_alias=True) for tool in gateway.tools])
    projected = project_gateway_tools(gateway, ARGUMENT_FIELDS)
    assert gateway.starts == gateway.lists == 1
    assert {tool.tool_name for tool in projected} == {
        f"TicketTools___{short}" for short in MODEL_TOOLS
    }
    for tool in projected:
        short = tool.tool_name.split("___", 1)[1]
        spec = tool.tool_spec
        schema = spec["inputSchema"]["json"]
        assert schema == {
            "type": "object",
            "properties": {k: {"type": "string"} for k in ARGUMENT_FIELDS[short]},
            "required": list(ARGUMENT_FIELDS[short]),
            "additionalProperties": False,
        }
        assert tool.mcp_client is gateway and tool.mcp_tool.name == tool.tool_name
        assert tool.mcp_tool is not next(
            t.mcp_tool for t in gateway.tools if t.tool_name == tool.tool_name
        )
        assert "actor" not in schema["properties"] and "run_id" not in schema["properties"]
    assert raw == [tool.mcp_tool.model_dump(by_alias=True) for tool in gateway.tools]
    assert "TicketTools___confirm_close" not in {tool.tool_name for tool in projected}


@pytest.mark.parametrize("scope", ("root", "actor"))
@pytest.mark.parametrize("explicit_false", (False, True), ids=("absent", "boolean_false"))
def test_additional_properties_absent_or_boolean_false_is_supported(scope, explicit_false):
    rows = copy.deepcopy(CAPTURE["tools"])
    raw = additional_schema(rows, scope)
    assert "additionalProperties" not in raw
    if explicit_false:
        raw["additionalProperties"] = False
    gateway = Gateway(rows)
    assert len(project_gateway_tools(gateway, ARGUMENT_FIELDS)) == 3
    assert gateway.starts == gateway.lists == 1
    if explicit_false:
        assert raw["additionalProperties"] is False
    else:
        assert "additionalProperties" not in raw


@pytest.mark.parametrize("scope,label,bad", ADDITIONAL_CASES, ids=ADDITIONAL_IDS)
def test_additional_properties_boundary_rejects_bad_type(scope, label, bad):
    rows = copy.deepcopy(CAPTURE["tools"])
    additional_schema(rows, scope)["additionalProperties"] = bad
    gateway = Gateway(rows)
    with pytest.raises(ValueError, match="Gateway|actor"):
        project_gateway_tools(gateway, ARGUMENT_FIELDS)
    assert gateway.starts == gateway.lists == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_actor",
        "required_run_id",
        "unexpected_property",
        "wrong_customer_type",
        "actor_spoof_schema",
        "unknown_tool",
        "duplicate_tool",
        "missing_tool",
        "bad_root_type",
        "unsupported_annotations",
        "unhashable_required",
    ],
)
def test_incomplete_or_untrusted_gateway_schema_fails_before_model(mutation):
    rows = copy.deepcopy(CAPTURE["tools"])
    target = next(row for row in rows if row["name"] == "TicketTools___list_tickets")
    schema = target["inputSchema"]
    if mutation == "missing_actor":
        schema["required"].remove("actor")
    elif mutation == "required_run_id":
        schema["required"].append("run_id")
    elif mutation == "unexpected_property":
        schema["properties"]["extra"] = {"type": "string"}
    elif mutation == "wrong_customer_type":
        schema["properties"]["customer_id"]["type"] = "object"
    elif mutation == "actor_spoof_schema":
        schema["properties"]["actor"]["properties"]["user_sub"]["type"] = "number"
    elif mutation == "unknown_tool":
        rows[0]["name"] = "TicketTools___untrusted"
    elif mutation == "duplicate_tool":
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "missing_tool":
        rows.pop()
    elif mutation == "unsupported_annotations":
        target["annotations"] = {"destructiveHint": True}
    elif mutation == "unhashable_required":
        schema["required"].append(["customer-a"])
    else:
        schema["type"] = "array"
    gateway = Gateway(rows)
    with pytest.raises(ValueError, match="Gateway|schema|Tool"):
        project_gateway_tools(gateway, ARGUMENT_FIELDS)
    assert gateway.starts == gateway.lists == 1
