"""agent.py — Strands Agent ECS Service

Production Strands agent running on ECS Fargate.
Exposes a FastAPI HTTP endpoint that the chat interface calls.

Auth inbound  : Cognito JWT (validated via JWKS)
Auth outbound :
  → AWS Agent Registry : IAM SigV4 (ECS task role, automatic via boto3)
  → MCP Server (ECS)   : IAM SigV4 (streamablehttp_client_with_sigv4)
  → S3                 : IAM SigV4 (ECS task role, automatic via boto3)

Startup sequence (once per ECS task):
  1. Query Registry for MCP record → extract and cache the server URL only
     (no connection at startup — tools are loaded selectively per request)

Per-request (runtime):
  2. Registry search (all types) for the user's intent
  3. Select top SKILL result → parse mcp_tools: from its SKILL.md frontmatter
  4. Connect MCP server → filter list_tools_sync() to only the declared tools
  5. Build Strands Agent with base tools + those specific MCP tools only
  6. Run agent → skill instructions drive tool calls

Design rationale — selective MCP tool loading:
  An MCP server may expose many tools. Loading all of them into every agent
  request wastes context tokens and presents the model with irrelevant choices.
  Each SKILL.md declares exactly which MCP tools it needs via `mcp_tools:` in
  its YAML frontmatter. The agent loads only those tools, keeping context lean.
  This pattern generalises: a registry with many MCP servers and many skills
  means each request only loads the tools its skill actually needs.
"""

import json
import logging
import os
import tempfile
import threading
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from boto3.session import Session
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwk, jwt
from pydantic import BaseModel
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from strands_tools import file_read

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("agent")

# ── Configuration from environment ───────────────────────────────────────────

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
REGISTRY_ARN = os.environ.get("REGISTRY_ARN", "")
SKILLS_BUCKET = os.environ.get("SKILLS_BUCKET", "")
_raw_jwks = os.environ.get("COGNITO_JWKS_URL", "")
COGNITO_JWKS_URL = _raw_jwks if _raw_jwks.startswith("https://") else ""  # skip JWT if not a real URL
LOADED_SKILLS_DIR = "/tmp/loaded_skills"

# ── AWS clients (use ECS task role automatically) ─────────────────────────────

boto_session = Session(region_name=AWS_REGION)
registry_client = boto_session.client("agent-registry-control")
search_client = boto_session.client("agent-registry")
s3_client = boto_session.client("s3")

# ── Globals populated at startup ─────────────────────────────────────────────

agent: Agent | None = None
_static_tools: list = []  # search_and_load_skill, file_read, python_exec
_agent_system_prompt: str = ""
_mcp_lock: threading.Lock = threading.Lock()
_discovered_mcp_url: str = ""  # MCP server URL read from Registry at startup
# (connection is made per-request, not at startup)


# ── Skill loader (reads SKILL.md from registry response + S3 artifacts) ──────


def _extract_skill_name(skill_md: str, fallback: str) -> str:
    in_fm = False
    for line in skill_md.splitlines():
        stripped = line.strip()
        if stripped == "---":
            if not in_fm:
                in_fm = True
                continue
            else:
                break
        if in_fm and stripped.startswith("name:"):
            return stripped[len("name:") :].strip()
    return fallback


def load_skill_from_registry(search_response: dict, record_index: int = 0) -> tuple[str, str]:
    """Stage skill from registry response to /tmp/loaded_skills/{name}/.

    SKILL.md content comes from registry inlineContent.
    Supporting files (references/, scripts/) come from S3.
    Returns (skill_dir, skill_md_content).
    """
    record = search_response["registryRecords"][record_index]
    agent_skills = record["descriptors"]["agentSkillsDefinition"]
    skill_md = agent_skills["additionalData"]["skillMd"]["data"]
    skill_name = _extract_skill_name(skill_md, record.get("name", "skill"))

    skill_dir = os.path.join(LOADED_SKILLS_DIR, skill_name)
    os.makedirs(skill_dir, exist_ok=True)

    # Write SKILL.md from registry
    with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
        f.write(skill_md)
    log.info("Written SKILL.md for %s", skill_name)

    # Download supporting files from S3 if bucket is configured
    if SKILLS_BUCKET:
        _download_skill_artifacts(skill_name, skill_dir)

    return skill_dir, skill_md


def _download_skill_artifacts(skill_name: str, skill_dir: str) -> None:
    """Download references/, scripts/, assets/ from S3 into skill_dir."""
    prefix = f"skills/{skill_name}/"
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=SKILLS_BUCKET, Prefix=prefix)
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel_path = key[len(prefix) :]  # strip "skills/{name}/"
                if not rel_path or rel_path.endswith("/"):
                    continue
                dest = os.path.join(skill_dir, rel_path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                s3_client.download_file(SKILLS_BUCKET, key, dest)
                log.info("Downloaded s3://%s/%s → %s", SKILLS_BUCKET, key, dest)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not download S3 artifacts for %s: %s", skill_name, exc)


# ── SigV4 MCP transport ───────────────────────────────────────────────────────


def _make_sigv4_mcp_client(url: str) -> MCPClient:
    """Create MCPClient using SigV4-signed streamable-HTTP transport.

    The MCP server is behind API Gateway with AWS_IAM auth, so requests must be
    signed with service="execute-api". The ECS task role has execute-api:Invoke
    permission scoped to the MCP API Gateway resource.

    Falls back to unsigned transport if streamable_http_sigv4 is unavailable
    (e.g., on localhost / dev).
    """
    try:
        from streamable_http_sigv4 import streamablehttp_client_with_sigv4

        credentials = boto_session.get_credentials()
        log.info("Using SigV4 (execute-api) transport for MCP server at %s", url)
        return MCPClient(
            lambda u=url, c=credentials: streamablehttp_client_with_sigv4(
                url=u,
                credentials=c,
                service="execute-api",
                region=AWS_REGION,
            )
        )
    except ImportError:
        from mcp.client.streamable_http import streamablehttp_client

        log.warning("streamable_http_sigv4 not installed — using unsigned transport (dev mode)")
        return MCPClient(lambda u=url: streamablehttp_client(u))


# ── MCP: discover URL at startup, connect selectively per-request ────────────


def _discover_mcp_url() -> str:
    """Query AWS Agent Registry for the MCP record and return the server URL.

    Called once at startup to cache the MCP server URL. No connection is made
    here — tools are loaded selectively per-request based on each skill's
    declared mcp_tools: frontmatter field.

    The registry crawls the MCP server at registration time and stores the
    MCP server manifest (modelcontextprotocol.io JSON schema) in
    descriptors.mcpServer.data. The URL is under remotes[0].url.
    """
    if not REGISTRY_ARN:
        log.warning("REGISTRY_ARN not set — cannot discover MCP URL from Registry")
        return ""
    registry_id = REGISTRY_ARN.split("/")[-1]
    try:
        response = search_client.search_discoverable_registry_records(
            registryIds=[registry_id],
            searchQuery="financial tools MCP server",
            maxResults=10,
        )
        for record in response.get("registryRecords", []):
            if record.get("recordType") == "MCP":
                # The registry crawls the MCP server and populates server.inlineContent
                # with the MCP server manifest (modelcontextprotocol.io schema).
                # The URL is in: descriptors.mcpServer.data (JSON) → remotes[0].url
                inline = record.get("descriptors", {}).get("mcpServer", {}).get("data", "")
                url = ""
                try:
                    if inline:
                        server_json = json.loads(inline)
                        remotes = server_json.get("remotes", [])
                        if remotes:
                            url = remotes[0].get("url", "")
                except Exception:  # noqa: BLE001, S110
                    pass

                if url:
                    log.info("Discovered MCP URL from Registry: %s", url)
                    return url
        log.warning("No MCP record found in Registry")
    except Exception as exc:  # noqa: BLE001
        log.warning("Registry MCP URL discovery failed: %s", exc)
    return ""


def _parse_mcp_tools_from_frontmatter(skill_md: str) -> list[str]:
    """Extract the mcp_tools: list from a SKILL.md YAML frontmatter block.

    Example frontmatter:
        mcp_tools:
          - get_financial_data
          - get_kpi_benchmarks

    Returns a list of tool name strings, or [] if the field is absent.
    """
    in_fm = False
    in_tools_block = False
    tool_names: list[str] = []

    for line in skill_md.splitlines():
        stripped = line.strip()
        if stripped == "---":
            if not in_fm:
                in_fm = True
                continue
            else:
                break  # end of frontmatter
        if not in_fm:
            continue
        if stripped.startswith("mcp_tools:"):
            in_tools_block = True
            continue
        if in_tools_block:
            if stripped.startswith("- "):
                tool_names.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("#"):
                in_tools_block = False  # next top-level key

    return tool_names


def _get_selective_mcp_tools(
    declared_tool_names: list[str],
) -> tuple[list, "MCPClient"]:
    """Connect to the MCP server and return only the declared tools.

    Creates a fresh MCPClient (FastMCP sessions expire on idle), lists all
    available tools, then filters to only those named in declared_tool_names.

    Args:
        declared_tool_names: Tool names from the skill's mcp_tools: frontmatter.

    Returns:
        (filtered_tools, client) — client must be stopped after the agent finishes.
    """
    if not _discovered_mcp_url:
        return [], None

    client = _make_sigv4_mcp_client(_discovered_mcp_url)
    client.start()
    all_tools = client.list_tools_sync()

    if not declared_tool_names:
        # Skill declared no specific tools — return all (fallback)
        log.info("No mcp_tools declared in skill — loading all %d MCP tools", len(all_tools))
        return all_tools, client

    name_set = set(declared_tool_names)
    selected = [t for t in all_tools if t.tool_name in name_set]
    available_names = [t.tool_name for t in all_tools]
    log.info(
        "MCP selective load: requested=%s available=%s loaded=%s",
        declared_tool_names,
        available_names,
        [t.tool_name for t in selected],
    )
    return selected, client


def _build_static_tools() -> None:
    """Initialise the static tool set and system prompt (called once at startup).

    MCP tools are NOT included here — they are loaded selectively per-request
    in _run() based on each skill's mcp_tools: frontmatter declaration.
    """

    @tool
    def search_and_load_skill(query: str) -> str:
        """Search the AWS Agent Registry for a skill and load it locally.

        Searches the unified AWS Agent Registry across all record types
        (SKILL, MCP, AGENT, CUSTOM) and returns results ranked by
        semantic relevance.

        The registry is a unified catalog. A search for "financial analysis"
        may return both SKILL records (workflow instructions) and MCP
        records (tool servers). Interpret results by type:
          - SKILL → load and follow its SKILL.md instructions
          - MCP          → informational; tools are already connected at startup
          - A2A / CUSTOM → informational; not actionable in this deployment

        Always select the top SKILL record to execute the task.
        The full SKILL.md is only loaded for the top match (progressive disclosure).

        Args:
            query: Natural language description of the task or skill needed.

        Returns:
            Ranked candidate list with types + SKILL.md for the top SKILL match.
        """
        registry_id = REGISTRY_ARN.split("/")[-1]

        # Search across all record types — this is the documented pattern.
        # The registry is a unified catalog (MCP, AGENT, SKILL, CUSTOM).
        # We select SKILL client-side after receiving the ranked results.
        response = search_client.search_discoverable_registry_records(
            registryIds=[registry_id],
            searchQuery=query,
            maxResults=10,
        )
        response.pop("ResponseMetadata", None)

        all_records = response.get("registryRecords", [])
        skill_records = [r for r in all_records if r.get("recordType") == "SKILL"]

        if not skill_records:
            return f"No SKILL records found for query: '{query}'. The registry may have no approved skill records yet."

        log.info(
            "Registry search: %d SKILL result(s) for '%s'",
            len(skill_records),
            query,
        )

        # Build the candidate list (name + description only — progressive disclosure:
        # full SKILL.md content is NOT loaded for every result, only the top match)
        candidates = []
        for i, rec in enumerate(skill_records):
            marker = " ← selected (top match)" if i == 0 else ""
            candidates.append(
                f"  {i + 1}. {rec.get('name', 'unknown')}{marker}\n     {rec.get('description', '(no description)')}"
            )
        candidate_block = "\n".join(candidates)

        # Load full SKILL.md only for the top result
        skill_dir, skill_md = load_skill_from_registry(response, record_index=0)
        abs_dir = os.path.abspath(skill_dir)
        skill_name = skill_records[0].get("name", "unknown")

        return (
            f"Registry returned {len(skill_records)} candidate skill(s) for '{query}':\n\n"
            f"{candidate_block}\n\n"
            f"--- Loaded skill: '{skill_name}' ---\n"
            f"Skill folder: {abs_dir}\n\n"
            f"SKILL.md instructions:\n\n{skill_md}\n\n"
            f"Use working_dir='{os.getcwd()}' when running code."
        )

    @tool
    def python_exec(code: str, working_dir: str = "") -> str:
        """Execute Python code in a subprocess and return the output.

        Args:
            code:        Python source code to execute.
            working_dir: Optional directory to run the code in.

        Returns:
            Combined stdout + stderr, or an error message.
        """
        import subprocess
        import traceback

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
                tmp.write(code)
                tmp_path = tmp.name

            kwargs: dict = {"timeout": 60, "capture_output": True, "text": True}
            if working_dir and os.path.isdir(working_dir):
                kwargs["cwd"] = working_dir

            result = subprocess.run(["python3", tmp_path], check=False, **kwargs)
            output = result.stdout
            if result.stderr:
                output += "\n[stderr]\n" + result.stderr
            return output.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: execution timed out (60s limit)"
        except Exception:  # noqa: BLE001
            return f"Error:\n{traceback.format_exc()}"
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    global _static_tools, _agent_system_prompt
    _static_tools = [search_and_load_skill, file_read, python_exec]
    _agent_system_prompt = (
        "You are a financial analyst agent with access to the AWS Agent Registry. "
        "The registry is a unified catalog with four record types:\n"
        "  • SKILL: step-by-step workflow instructions for a task. "
        "    Always search for and follow a SKILL record first.\n"
        "  • MCP: tool servers. If an MCP record appears in search results, "
        "    the tools it describes are already available to you by name — "
        "    call them directly as instructed by the skill.\n"
        "  • AGENT / CUSTOM: informational only in this deployment.\n"
        "When the user asks you to perform a task: call search_and_load_skill, "
        "select the top SKILL result, and follow its SKILL.md exactly. "
        "MCP tools are pre-loaded for this request — call them as the skill instructs."
    )


# ── FastAPI app with lifespan startup/shutdown ────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, _discovered_mcp_url
    log.info("Starting up — discovering MCP server URL from registry...")
    try:
        _discovered_mcp_url = _discover_mcp_url()
    except Exception as exc:  # noqa: BLE001
        log.warning("MCP URL discovery failed at startup: %s", exc)
        _discovered_mcp_url = ""
    _build_static_tools()
    # Build a minimal warmup agent (no MCP tools) just to confirm startup is healthy
    agent = Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=AWS_REGION),
        tools=_static_tools,
        system_prompt=_agent_system_prompt,
    )
    log.info("Agent ready. MCP URL: %s", _discovered_mcp_url or "(not discovered)")
    yield
    # No shared MCP client to shut down — each request manages its own client lifecycle


app = FastAPI(title="Financial Analyst Agent", lifespan=lifespan)
bearer_scheme = HTTPBearer(auto_error=False)


# ── JWT validation (Cognito) ──────────────────────────────────────────────────

_jwks_cache: dict | None = None


def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None and COGNITO_JWKS_URL:
        import urllib.request

        with urllib.request.urlopen(COGNITO_JWKS_URL, timeout=5) as r:
            _jwks_cache = json.loads(r.read())
    return _jwks_cache or {}


def _verify_cognito_jwt(token: str) -> dict:
    """Validate a Cognito JWT. Raises HTTPException on failure."""
    if not COGNITO_JWKS_URL:
        # No Cognito configured — skip validation (dev mode)
        return {}
    try:
        jwks = _get_jwks()
        # Decode header to get kid
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        key = next((k for k in jwks.get("keys", []) if k["kid"] == kid), None)
        if not key:
            raise HTTPException(status_code=401, detail="Unknown token signing key")
        public_key = jwk.construct(key)
        claims = jwt.decode(token, public_key, algorithms=["RS256"])
        return claims
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


def require_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),  # noqa: B008
) -> dict:
    """FastAPI dependency — validates Bearer JWT if Cognito is configured."""
    if not COGNITO_JWKS_URL:
        return {}  # dev mode: no auth required
    if not creds:
        raise HTTPException(status_code=401, detail="Authorization header required")
    return _verify_cognito_jwt(creds.credentials)


# ── Request / response models ─────────────────────────────────────────────────


class InvokeRequest(BaseModel):
    message: str
    history: list = []  # [{role: "user"|"assistant", content: "..."}]


class InvokeResponse(BaseModel):
    response: str


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "healthy", "agent_ready": agent is not None}


@app.post("/invoke", response_model=InvokeResponse)
def invoke(
    req: InvokeRequest,
    claims: dict = Depends(require_auth),  # noqa: B008
):
    """Invoke the Strands agent with a user message."""
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not ready")
    log.info("Invoke: %s (user=%s)", req.message[:80], claims.get("sub", "anon"))
    result = agent(req.message)
    return InvokeResponse(response=str(result))


@app.post("/invoke/stream")
def invoke_stream(
    req: InvokeRequest,
    claims: dict = Depends(require_auth),  # noqa: B008
):
    """Invoke the Strands agent and stream SSE progress events back to the caller.

    Event types:
      step   — a tool call step (shown as live progress in the UI)
      result — the final answer
      error  — something went wrong
    """
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not ready")

    log.info("Stream invoke: %s (user=%s)", req.message[:80], claims.get("sub", "anon"))

    def _sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    def generate():
        import queue as _queue

        q: _queue.Queue[str | None] = _queue.Queue()
        result_holder: list = []
        error_holder: list = []

        class _SSECallback:
            """Strands callback_handler — receives **kwargs per the PrintingCallbackHandler contract."""

            def __call__(self, **kwargs):
                # Log all kwargs keys for debugging
                keys = [k for k, v in kwargs.items() if v]
                if keys:
                    log.info("SSE callback kwargs keys: %s", keys)

                # Tool use: nested under event.contentBlockStart.start.toolUse
                event_data = kwargs.get("event", {})
                tool_use = event_data.get("contentBlockStart", {}).get("start", {}).get("toolUse")
                if tool_use:
                    name = tool_use.get("name", "")
                    inp = tool_use.get("input", {}) or {}
                    log.info("SSE step — tool: %s input: %s", name, inp)
                    label = _tool_label(name, inp)
                    q.put(_sse("step", {"text": label, "tool": name}))

                complete = kwargs.get("complete", False)
                data = kwargs.get("data", "")
                if complete and data:
                    log.info("SSE complete chunk received, len=%d", len(data))

        cb = _SSECallback()

        def _step(text: str, icon: str = "") -> None:
            """Emit a named step to the SSE queue."""
            label = (icon + " " + text).strip()
            log.info("Step: %s", label)
            q.put(_sse("step", {"text": label}))

        def _run():
            per_request_mcp_client = None
            try:
                from strands import Agent
                from strands.models import BedrockModel

                # ── STEP 1: Registry semantic search — across all record types ──
                # Unified catalog search: returns SKILL, MCP, AGENT, and
                # CUSTOM records ranked by semantic (vector) similarity to the
                # user's message. Client-side we select the top SKILL.
                registry_id = REGISTRY_ARN.split("/")[-1]
                _step("Semantic search on AWS Agent Registry…", "🔍")
                search_resp = search_client.search_discoverable_registry_records(
                    registryIds=[registry_id],
                    searchQuery=req.message,
                    maxResults=10,
                )
                search_resp.pop("ResponseMetadata", None)
                all_records = search_resp.get("registryRecords", [])
                skill_records = [r for r in all_records if r.get("recordType") == "SKILL"]

                # ── STEP 2: Parse skill frontmatter → get declared MCP tools ──
                declared_tool_names: list[str] = []
                skill_name_found = "(none)"
                if skill_records:
                    top_skill_md = (
                        skill_records[0]
                        .get("descriptors", {})
                        .get("agentSkillsDefinition", {})
                        .get("additionalData", {})
                        .get("skillMd", {})
                        .get("data", "")
                    )
                    declared_tool_names = _parse_mcp_tools_from_frontmatter(top_skill_md)
                    skill_name_found = skill_records[0].get("name", "unknown")
                    log.info(
                        "Skill '%s' declares MCP tools: %s",
                        skill_name_found,
                        declared_tool_names,
                    )

                # Emit structured search results for UI visualization.
                # NOTE: "top_match" is the top-ranked SKILL result used only
                # to pre-load MCP tools for this request. The Strands agent will
                # independently call search_and_load_skill and decide which skill
                # (if any) to actually execute. They may differ.
                _step(
                    f"Registry returned {len(all_records)} record(s)"
                    + (f" — top skill candidate: '{skill_name_found}'" if skill_records else " — no skill records"),
                    "📋",
                )
                q.put(
                    _sse(
                        "search",
                        {
                            "query": req.message[:120],
                            "total_found": len(all_records),
                            "candidates": [
                                {
                                    "name": r.get("name", "?"),
                                    "type": r.get("recordType", "?"),
                                    "description": r.get("description", "")[:120],
                                    "top_match": (
                                        r.get("recordType") == "SKILL"
                                        and bool(skill_records)
                                        and r.get("name") == skill_records[0].get("name")
                                    ),
                                }
                                for r in all_records[:8]
                            ],
                            "top_skill": skill_name_found,
                            "mcp_tools": declared_tool_names,
                        },
                    )
                )

                # ── STEP 3: Selective MCP connect ─────────────────────────────
                # Load ONLY the tools this skill needs — not the entire MCP server.
                mcp_tools: list = []
                if _discovered_mcp_url and declared_tool_names:
                    try:
                        mcp_tools, per_request_mcp_client = _get_selective_mcp_tools(declared_tool_names)
                        _step(
                            "MCP connected — loaded "
                            + str(len(mcp_tools))
                            + "/"
                            + str(len(declared_tool_names))
                            + " declared tools: "
                            + ", ".join(t.tool_name for t in mcp_tools),
                            "🔌",
                        )
                    except Exception as mcp_exc:  # noqa: BLE001
                        log.warning("MCP unavailable: %s", mcp_exc)
                        _step("MCP unavailable — continuing without data tools", "⚠️")
                elif _discovered_mcp_url and not declared_tool_names:
                    _step("Skill declares no MCP tools — skipping MCP connection", "ℹ️")

                tools = [*_static_tools, *mcp_tools]

                # ── STEP 4: Build agent with selective tool set ───────────────
                prior_messages = [
                    {
                        "role": "assistant" if turn.get("role") == "ai" else turn.get("role", "user"),
                        "content": [{"text": turn.get("content", "")}],
                    }
                    for turn in req.history
                    if turn.get("role") in ("user", "assistant", "ai") and turn.get("content")
                ]
                if mcp_tools:
                    mcp_detail = f"{len(mcp_tools)} MCP + {len(_static_tools)} base"
                elif not _discovered_mcp_url:
                    mcp_detail = f"{len(_static_tools)} base (MCP server not yet registered)"
                elif declared_tool_names:
                    mcp_detail = f"{len(_static_tools)} base (MCP unavailable)"
                else:
                    mcp_detail = f"{len(_static_tools)} base (skill needs no MCP tools)"
                _step(
                    f"Building agent · {len(tools)} tools ({mcp_detail}) · {MODEL_ID}",
                    "🤖",
                )
                a = Agent(
                    model=BedrockModel(model_id=MODEL_ID, region_name=AWS_REGION),
                    tools=tools,
                    system_prompt=_agent_system_prompt,
                    callback_handler=cb,
                    messages=prior_messages,
                )

                # ── STEP 5: Invoke Bedrock ────────────────────────────────────
                _step("Sending to Amazon Bedrock · " + MODEL_ID, "⚡")
                r = a(req.message)

                _step("Agent reasoning complete — preparing response", "✅")
                result_holder.append(str(r))
            except Exception as exc:
                log.exception("Stream agent error")
                error_holder.append(str(exc))
            finally:
                # Always stop the per-request MCP client to release the session
                if per_request_mcp_client is not None:
                    try:
                        per_request_mcp_client.stop(None, None, None)
                        log.info("Per-request MCP client stopped.")
                    except Exception:  # noqa: BLE001, S110
                        pass
                q.put(None)  # sentinel

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        while True:
            try:
                item = q.get(timeout=120)
            except Exception:  # noqa: BLE001
                break
            if item is None:
                break
            yield item

        if error_holder:
            yield _sse("error", {"text": error_holder[0]})
        else:
            yield _sse("result", {"text": result_holder[0] if result_holder else ""})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _tool_label(name: str, inp: dict | None = None) -> str:
    """Human-readable description of a tool call for the UI progress feed."""
    if inp is None:
        inp = {}
    if name == "search_and_load_skill":
        q = inp.get("query", "")
        suffix = ': "' + q + '"' if q else ""
        return "🔍 Searching AWS Agent Registry (all types)" + suffix + "…"
    if name == "get_financial_data":
        period = inp.get("period", "")
        suffix = " for " + period if period else ""
        return "📊 Fetching P&L data from MCP server" + suffix + "…"
    if name == "get_kpi_benchmarks":
        return "📐 Fetching KPI benchmark thresholds from MCP server…"
    if name == "python_exec":
        lines = inp.get("code", "").strip().splitlines()
        preview = lines[0][:60] if lines else ""
        suffix = ": " + preview if preview else ""
        return "⚙️  Running Python calculation" + suffix + "…"
    if name == "file_read":
        path = inp.get("path", "")
        suffix = ": " + path if path else ""
        return "📄 Reading skill file" + suffix + "…"
    return "🔧 Calling tool: " + name + "…"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
