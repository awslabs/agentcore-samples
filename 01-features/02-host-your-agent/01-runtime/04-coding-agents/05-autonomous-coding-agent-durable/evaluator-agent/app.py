"""Evaluator Agent — AgentCore Runtime entrypoint (standalone, read-only).

A first-class, separate agent from the coding agent: its own runtime, image, IAM role,
logs, and cost line. It runs AFTER the coding agent has implemented a ticket and the
deterministic test gate has passed. The orchestrator (Lambda Durable Function) invokes it
deterministically — no LLM decides when review happens.

It is strictly READ-ONLY: it reads the code in the shared mount (Read/Glob/Grep), reasons
with Claude on Bedrock, and returns a structured verdict. It has NO sandbox tools, NO
command execution, NO write/edit, and NO ability to invoke other runtimes — and its IAM
role grants only what that requires (read the mount + invoke Bedrock).

Contract: payload {"ticket_prefix", optional "prompt"}; context.session_id = session id.
Returns {"result": <text ending in a {verdict, issues, lessons} JSON object>, ...}.
"""
import os
import asyncio

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ResultMessage,
)
try:
    from claude_agent_sdk import ToolUseBlock
except ImportError:
    ToolUseBlock = None

app = BedrockAgentCoreApp()

MOUNT_BASE = os.environ.get("MOUNT_PATH", "/mnt/shared")
AGENT_TIMEOUT_SECONDS = int(os.environ.get("AGENT_TIMEOUT", "900"))

SYSTEM_PROMPT = (
    "You are a code review / evaluation agent. You run AFTER a coding agent has implemented "
    "a ticket and its tests have passed. You are READ-ONLY — you cannot write, edit, or "
    "execute code; you only read files in your working directory and reason about them.\n\n"
    "Judge whether the implementation correctly and cleanly satisfies the ticket. Focus on: "
    "correctness vs. the ticket, obvious bugs, missing tests/edge cases, and clear "
    "code-quality problems. Be concise and specific (cite file:line).\n\n"
    "Also capture LESSONS: durable, repo-LEVEL takeaways that would help a future coding "
    "agent working on THIS repository on a DIFFERENT ticket. Lessons must be general and "
    "reusable — facts about the repo's conventions, structure, build/test setup, or key "
    "APIs. GOOD: 'NamedColor (Sources/Color.swift) is the canonical color source; new color "
    "features should map to it.' BAD (too ticket-specific — omit): 'Fixed off-by-one at "
    "Theme.swift:121.' Omit lessons if you have no durable insight.\n\n"
    "You MUST end your response with a single JSON object on its own line, no prose around it:\n"
    '{"verdict": "approve" | "request_changes", "issues": ["actionable issue", ...], '
    '"lessons": ["durable repo-level lesson", ...]}\n'
    "Use \"approve\" when the implementation is correct and complete; \"request_changes\" "
    "otherwise. The issues feed back to the coding agent; the lessons are saved to memory."
)

# Ticket-prefixed reasoning stream → CloudWatch (the demo viz scopes by ticket).
_ticket_prefix = ""


def _stream(kind: str, text: str):
    text = (text or "").strip().replace("\n", "\\n")
    if text:
        print(f"[AGENT|{_ticket_prefix}|{kind}] {text[:8000]}", flush=True)


def _bedrock_env() -> dict:
    model = os.environ.get("BEDROCK_MODEL", "global.anthropic.claude-opus-4-8")
    region = os.environ.get("AWS_REGION", "us-east-1")
    home = os.environ.get("HOME") or "/tmp/evalhome"  # nosec B108 — isolated container, /tmp not shared
    os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
    return {
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "AWS_REGION": region,
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": model,
        "HOME": home,
    }


async def _run(prompt: str, work_dir: str) -> dict:
    os.makedirs(work_dir, exist_ok=True)
    env = _bedrock_env()
    stderr_lines: list[str] = []
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        permission_mode="bypassPermissions",
        cwd=work_dir,
        allowed_tools=["Read", "Glob", "Grep"],   # read-only
        disallowed_tools=["Write", "Edit", "Bash", "Monitor", "WebFetch", "WebSearch"],
        model=env["ANTHROPIC_MODEL"],
        max_turns=30,
        setting_sources=[],
        env=env,
        stderr=lambda line: stderr_lines.append(line),
    )

    transcript: list[str] = []
    result_text = ""
    start = asyncio.get_event_loop().time()
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                if asyncio.get_event_loop().time() - start > AGENT_TIMEOUT_SECONDS:
                    raise TimeoutError(f"evaluator exceeded {AGENT_TIMEOUT_SECONDS}s")
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            transcript.append(block.text)
                            _stream("REASONING", block.text)
                        elif ToolUseBlock is not None and isinstance(block, ToolUseBlock):
                            nm = getattr(block, "name", "tool")
                            arg = (getattr(block, "input", {}) or {}).get("pattern") \
                                or (getattr(block, "input", {}) or {}).get("path") or ""
                            _stream("TOOL", f"{nm}: {str(arg)[:200]}")
                elif isinstance(msg, ResultMessage):
                    result_text = getattr(msg, "result", "") or ""
    except TimeoutError as e:
        return {"error": str(e), "transcript": transcript, "timed_out": True}
    except Exception:
        raise RuntimeError("CLI stderr:\n" + "\n".join(stderr_lines[-50:]))

    return {"result": result_text or "\n".join(transcript), "transcript": transcript}


@app.entrypoint
def invoke(payload, context):
    """payload: {"ticket_prefix", ["prompt"]}; context.session_id = inbound session id."""
    global _ticket_prefix
    ticket = payload.get("ticket_prefix", "")
    if not ticket:
        return {"error": "ticket_prefix is required"}
    _ticket_prefix = ticket

    # Confine to the ticket's subdir of the shared mount (read-only review scope).
    work_dir = os.path.realpath(os.path.join(MOUNT_BASE, ticket))
    base = os.path.realpath(MOUNT_BASE)
    if work_dir != base and not work_dir.startswith(base + os.sep):
        return {"error": f"ticket_prefix escapes mount: {ticket!r}"}

    prompt = payload.get("prompt") or (
        f"Review the implementation of ticket {ticket} in your working directory against "
        f"its requirements, then return your verdict JSON."
    )
    try:
        out = asyncio.run(_run(prompt, work_dir))
        out["ticket_prefix"] = ticket
        return out
    except Exception as e:
        import traceback
        print(f"[ERROR] evaluator failed: {e}\n{traceback.format_exc()}")
        return {"error": f"evaluator execution failed: {type(e).__name__}"}


if __name__ == "__main__":
    app.run()
