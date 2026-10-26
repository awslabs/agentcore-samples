"""Evaluate skill selection and instruction following for the HR Assistant.

Demonstrates the two built-in AgentCore skill evaluators using two SDK interfaces:

  1. EvaluationClient
       Evaluate a single recorded session on demand. For the PTO session, the
       script first extracts the exact trace attributes each evaluator receives
       (invoked_skill, available_skills, user_message, skill_content, context)
       and prints them so the evaluator inputs are visible before showing scores.

  2. BatchEvaluationRunner
       Invoke the same scenarios as a dataset and return aggregate scores per
       evaluator across all sessions in one service-side batch job.

Prerequisite:
    python ../utils/deploy.py --skills-dir skills --config-output agent_config.json

Usage:
    python evaluate.py [--region REGION] [--config PATH] [--wait SECONDS]
    python evaluate.py --prompt "..." --expected-skill SKILL_NAME
"""

import argparse
import json
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import boto3
from boto3.session import Session

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_CONFIG = _SCRIPT_DIR / "agent_config.json"
_RESULTS_DIR = _SCRIPT_DIR / "results"

_EVALUATOR_IDS = (
    "Builtin.SkillSelectionAccuracy",
    "Builtin.SkillInstructionFollowing",
)

# SkillInvoked is a deterministic evaluator from the Strands Evals SDK
# (the `strands-agents-evals` distribution, imported as `strands_evals`). It is
# NOT an AgentCore `Builtin.*` evaluator, so it runs client-side here — never
# through the AgentCore evaluate API, the service-side batch job, or the
# `agentcore run eval` CLI. Unlike the two LLM built-ins, it scores every
# session, including the no-skill control, with a deterministic yes/no.
_STRANDS_SKILL_INVOKED_ID = "Strands.SkillInvoked"

# The positive prompts name the expected skill, matching the reference implementation
# and keeping this introductory evaluator sample deterministic.
_SCENARIOS = (
    {
        "name": "pto-planning",
        "prompt": "Use the pto-planning skill. What is the available PTO balance for employee EMP-001?",
        "expected_skill": "pto-planning",
    },
    {
        "name": "benefits-advisor",
        "prompt": (
            "What does Acme's health insurance cover, who is eligible, and what does the employee pay? "
            "Use the benefits-advisor skill."
        ),
        "expected_skill": "benefits-advisor",
    },
    {
        "name": "no-skill-control",
        "prompt": "Show the January 2026 pay stub for employee EMP-001.",
        "expected_skill": None,
    },
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the built-in AgentCore skill evaluators")
    parser.add_argument("--region", default=None, help="AWS region (defaults to the deployment config)")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG), help="Path to the skill-enabled agent config")
    parser.add_argument("--wait", type=int, default=150, help="Seconds to wait for telemetry (default: 150)")
    parser.add_argument("--prompt", default=None, help="Run one custom prompt instead of the built-in scenarios")
    parser.add_argument(
        "--expected-skill",
        choices=("pto-planning", "benefits-advisor", "none"),
        default=None,
        help="Expected skill for --prompt; use 'none' when no skill should load",
    )
    args = parser.parse_args()
    if bool(args.prompt) != bool(args.expected_skill):
        parser.error("--prompt and --expected-skill must be provided together")
    return args


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Agent config not found at {path}. Deploy the skill-enabled agent first.")
    config = json.loads(path.read_text())
    if not config.get("skills_enabled"):
        raise ValueError(f"{path} is not a skill-enabled deployment. Deploy with --skills-dir skills.")
    return config


def _invoke_agent(client: Any, agent_arn: str, session_id: str, prompt: str) -> str:
    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
    )
    raw = response["response"].read().decode("utf-8")
    parts: list[str] = []
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        chunk: Any = line[len("data: ") :]
        try:
            chunk = json.loads(chunk)
        except json.JSONDecodeError:
            pass
        parts.append(str(chunk))
    return "".join(parts) if parts else raw


def _query_session_spans(
    logs_client: Any,
    log_group: str,
    session_id: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Fetch all span records for a session from the unified runtime log group."""
    query = f"""fields @message
| filter @message like "{session_id}"
| sort @timestamp asc
| limit 1000"""
    response = logs_client.start_query(
        logGroupName=log_group,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()),
        queryString=query,
    )
    query_id = response["queryId"]
    for _ in range(30):
        result = logs_client.get_query_results(queryId=query_id)
        if result["status"] == "Complete":
            records = []
            for row in result.get("results", []):
                message = next((f["value"] for f in row if f["field"] == "@message"), None)
                if not message:
                    continue
                try:
                    doc = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if isinstance(doc, dict):
                    records.append(doc)
            return records
        if result["status"] in {"Failed", "Cancelled", "Timeout"}:
            raise RuntimeError(f"CloudWatch query ended with status {result['status']}")
        time.sleep(2)
    logs_client.stop_query(queryId=query_id)
    raise TimeoutError("CloudWatch query timed out")


def _read_available_skills(skills_dir: Path) -> list[str]:
    """Read name and description from each SKILL.md frontmatter in skills_dir."""
    skills = []
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        name = description = ""
        in_front = False
        for line in skill_file.read_text().splitlines():
            if line.strip() == "---":
                if not in_front:
                    in_front = True
                    continue
                else:
                    break
            if in_front:
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
        if name:
            skills.append(f"{name}: {description}" if description else name)
    return skills


def _extract_skills_event(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the strands.telemetry.tracer event that captures the skills tool call.

    The unified runtime log group stores OTel log events from the Strands tracer.
    The skills call appears as a record where body.input contains a tool message
    whose content includes skill_name (i.e., the result of the skills tool call).
    """
    for record in records:
        if record.get("eventName") != "strands.telemetry.tracer":
            continue
        body = record.get("body", {})
        if not isinstance(body, dict):
            continue
        for msg in body.get("input", {}).get("messages", []):
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", {})
            content_str = content.get("content", "") if isinstance(content, dict) else str(content)
            if "skill_name" in content_str:
                return record
    return None


def _extract_skill_signals(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract invoked_skill, user_message, and skill_content from session spans.

    Pure extraction (no printing). Returns {} when the session has no skills
    tool-call event (for example the no-skill control). Shared by the verbose
    span display and the Strands SkillInvoked trajectory builder so both read the
    same signal.
    """
    event = _extract_skills_event(records)
    if not event:
        return {}

    body = event.get("body", {})
    attrs = event.get("attributes", {})

    # invoked_skill: the strands.telemetry.tracer event stores the skills tool-call
    # result in body.input.messages[role=tool].content.content as a JSON-encoded
    # string: {"skill_name": "pto-planning"}.  Fall back to body.message for the
    # gen_ai.choice variant where message is a JSON array of tool_use content blocks.
    invoked_skill = "N/A"
    for msg in body.get("input", {}).get("messages", []):
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", {})
        content_str = content.get("content", "") if isinstance(content, dict) else str(content)
        try:
            parsed = json.loads(content_str) if content_str else {}
            if isinstance(parsed, dict) and "skill_name" in parsed:
                invoked_skill = parsed["skill_name"]
                break
        except (json.JSONDecodeError, TypeError):
            pass
    if invoked_skill == "N/A":
        message_raw = body.get("message", "")
        try:
            blocks = json.loads(message_raw) if isinstance(message_raw, str) else message_raw
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "skills":
                        tool_input = block.get("input", {})
                        if isinstance(tool_input, str):
                            tool_input = json.loads(tool_input)
                        if isinstance(tool_input, dict):
                            invoked_skill = tool_input.get("skill_name", "N/A")
                        break
        except (json.JSONDecodeError, TypeError):
            pass

    # user_message: Strands encodes user content as {"content": "[{\"text\": \"...\"}]"}.
    # Scan all records for the earliest user-role message and decode accordingly.
    user_message = "N/A"
    for record in records:
        body_r = record.get("body", {})
        if not isinstance(body_r, dict):
            continue
        for msg in body_r.get("input", {}).get("messages", []):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            text = ""
            if isinstance(content, dict):
                inner = content.get("content", "")
                try:
                    blocks = json.loads(inner) if isinstance(inner, str) else inner
                    if isinstance(blocks, list):
                        text = next((b.get("text", "") for b in blocks if isinstance(b, dict) and "text" in b), "")
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(content, list):
                text = next((c.get("text", "") for c in content if isinstance(c, dict) and "text" in c), "")
            elif isinstance(content, str):
                text = content
            if text:
                user_message = text[:200]
                break
        if user_message != "N/A":
            break

    # skill_content: the skills tool returns the SKILL.md body (without frontmatter) in
    # body.output.messages[0].content.message of the same strands event.
    skill_content = "N/A"
    for out_msg in body.get("output", {}).get("messages", []):
        out_content = out_msg.get("content", {})
        message_str = out_content.get("message", "") if isinstance(out_content, dict) else ""
        try:
            out_blocks = json.loads(message_str) if isinstance(message_str, str) else message_str
            if isinstance(out_blocks, list):
                text = next((b.get("text", "") for b in out_blocks if isinstance(b, dict) and "text" in b), "")
                if text:
                    skill_content = text
                    break
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "traceId": event.get("traceId"),
        "spanId": event.get("spanId"),
        "session.id": attrs.get("session.id"),
        "invoked_skill": invoked_skill,
        "user_message": user_message,
        "skill_content": skill_content,
    }


def _print_skill_span_attributes(
    records: list[dict[str, Any]],
    available_skills: list[str] | None = None,
) -> dict[str, Any]:
    """Display the trace data each skill evaluator receives.

    SkillSelectionAccuracy  reads: invoked_skill, available_skills, user_message, context
    SkillInstructionFollowing reads: invoked_skill, skill_content, context

    Returns a dict with the extracted attribute values so callers can save them.
    """
    signals = _extract_skill_signals(records)
    if not signals:
        print("  No skills tool-call event found in session records.")
        return {}

    skill_content = signals["skill_content"]
    print("\n  Skills tool-call event (inputs the evaluators use):")
    print(f"    traceId        : {signals.get('traceId') or 'N/A'}")
    print(f"    spanId         : {signals.get('spanId') or 'N/A'}")
    print(f"    session.id     : {signals.get('session.id') or 'N/A'}")
    print()
    print("  Trace-derived evaluator signals:")
    print(f"    invoked_skill  : {signals['invoked_skill']}")
    print(f"    user_message   : {signals['user_message']!r}")
    print(f"    skill_content  : {skill_content[:200]!r}")
    print(f"    context        : ({len(records)} log records in this session span)")
    if available_skills:
        print()
        print("  Configured skill catalog (deployment context):")
        for s in available_skills:
            print(f"    - {s}")
        print()
        print("  Note: Strands emits the runtime available_skills catalog natively in the trace.")
        print("        AgentCore derives the available_skills placeholder for SkillSelectionAccuracy")
        print("        service-side from those spans — not from this local catalog listing.")
    return {
        "traceId": signals.get("traceId"),
        "spanId": signals.get("spanId"),
        "invoked_skill": signals["invoked_skill"],
        "configured_skills": available_skills or [],
        "user_message": signals["user_message"],
        "skill_content": skill_content,
    }


def _print_eval_results(scenario_name: str, evaluator_id: str, results: list[dict[str, Any]]) -> None:
    if not results:
        print(f"  {scenario_name:<20} {evaluator_id:<38} SKIPPED (0 results)")
        return
    for result in results:
        value = result.get("value", "N/A")
        label = result.get("label", "N/A")
        error = result.get("errorCode")
        if error:
            print(f"  {scenario_name:<20} {evaluator_id:<38} ERR:{error}")
            print(f"    {result.get('errorMessage', '')[:180]}")
        else:
            print(f"  {scenario_name:<20} {evaluator_id:<38} {value!s:<5} {label}")
            explanation = (result.get("explanation") or "")[:220]
            if explanation:
                print(f"    {explanation}")


def _build_skill_trajectory(session_id: str, invoked_skill: str | None, skill_content: str | None) -> Any:
    """Rebuild a Strands Evals trajectory from the skills span this session emitted.

    SkillInvoked normally reads a live Strands `Session`, but this script only has
    the deployed runtime's CloudWatch spans. We reconstruct the minimal trajectory
    it needs: one ToolExecutionSpan for the `skills` tool call (argument key
    `skill_name`) whose result is the loaded SKILL.md body. Sessions with no skill
    invocation (the no-skill control) get an empty trajectory, which SkillInvoked
    correctly scores as "not invoked" (0.0).
    """
    from strands_evals.types.trace import (
        Session,
        SpanInfo,
        ToolCall,
        ToolExecutionSpan,
        ToolResult,
        Trace,
    )

    now = datetime.now(timezone.utc)
    spans: list[Any] = []
    if invoked_skill and invoked_skill != "N/A" and skill_content and skill_content != "N/A":
        spans.append(
            ToolExecutionSpan(
                span_info=SpanInfo(session_id=session_id, start_time=now, end_time=now),
                tool_call=ToolCall(name="skills", arguments={"skill_name": invoked_skill}),
                tool_result=ToolResult(content=skill_content, error=None),
            )
        )
    return Session(
        session_id=session_id,
        traces=[Trace(spans=spans, trace_id=session_id, session_id=session_id)],
    )


def _run_skill_invoked(trajectory: Any, skill_name: str) -> list[Any]:
    """Run the deterministic Strands SkillInvoked evaluator against a trajectory."""
    from strands_evals.evaluators import SkillInvoked
    from strands_evals.types.evaluation import EvaluationData

    case = EvaluationData(input="", actual_trajectory=trajectory)
    return SkillInvoked(skill_name=skill_name).evaluate(case)


def _evaluate_skill_invoked(
    session: dict[str, Any],
    skill_signals: dict[str, Any],
    configured_skills: list[str],
    failures: list[str],
) -> list[dict[str, Any]]:
    """Score one session with the Strands SkillInvoked evaluator and validate it.

    Positive scenarios assert the expected skill was invoked (score 1.0). The
    no-skill control asserts that none of the deployed skills was invoked (score
    0.0 each) — a deterministic true-negative the LLM built-ins can only skip.
    """
    trajectory = _build_skill_trajectory(
        session["session_id"],
        skill_signals.get("invoked_skill"),
        skill_signals.get("skill_content"),
    )

    if session["expected_skill"]:
        checks = [(session["expected_skill"], 1.0)]
    else:
        checks = [(name, 0.0) for name in configured_skills]

    recorded: list[dict[str, Any]] = []
    for skill_name, expected_score in checks:
        outputs = _run_skill_invoked(trajectory, skill_name)
        for out in outputs:
            label = "Invoked" if out.score >= 1.0 else "Not invoked"
            evaluator_label = f"{_STRANDS_SKILL_INVOKED_ID}({skill_name})"
            print(f"  {session['name']:<20} {evaluator_label:<38} {out.score!s:<5} {label}")
            if out.reason:
                print(f"    {out.reason}")
            recorded.append(
                {
                    "skill_name": skill_name,
                    "score": out.score,
                    "test_pass": out.test_pass,
                    "reason": out.reason,
                }
            )
            if out.score != expected_score:
                failures.append(
                    f"{session['name']} / {evaluator_label}: expected score {expected_score}, got {out.score}"
                )
    return recorded


def main() -> int:
    args = _parse_args()
    try:
        config = _load_config(Path(args.config).expanduser().resolve())
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    config_region = config.get("region")
    if args.region and config_region and args.region != config_region:
        print(
            f"ERROR: --region {args.region} does not match the deployed runtime region {config_region}. "
            f"Omit --region or pass --region {config_region}.",
            file=sys.stderr,
        )
        return 1

    region = args.region or config_region or Session().region_name or "us-east-1"
    agentcore_client = boto3.client("bedrock-agentcore", region_name=region)
    logs_client = boto3.client("logs", region_name=region)
    _RESULTS_DIR.mkdir(exist_ok=True)

    scenarios = _SCENARIOS
    if args.prompt:
        expected_skill = None if args.expected_skill == "none" else args.expected_skill
        scenarios = (
            {
                "name": "custom-prompt",
                "prompt": args.prompt,
                "expected_skill": expected_skill,
            },
        )

    print("=" * 88)
    print("HR Assistant — Agent Skills Evaluation")
    print("=" * 88)
    print(f"Region  : {region}")
    print(f"Runtime : {config['agent_id']}")
    print(f"Skills  : {', '.join(config.get('skills', []))}")

    # ============================================================
    # 1. Invoke scenarios
    # ============================================================

    print("\n[1/4] Invoking scenarios ...")
    sessions = []
    for scenario in scenarios:
        session_id = f"skill-eval-{uuid.uuid4()}"
        print(f"\n  [{scenario['name']}] session={session_id}")
        start = datetime.now(timezone.utc)
        response = _invoke_agent(agentcore_client, config["agent_arn"], session_id, scenario["prompt"])
        print(f"  Response: {response[:180]}")
        sessions.append({**scenario, "session_id": session_id, "start": start, "response": response})

    # ============================================================
    # 2. Wait for telemetry ingestion
    # ============================================================

    print(f"\n[2/4] Waiting {args.wait}s for AgentCore telemetry ingestion ...")
    time.sleep(args.wait)

    # ============================================================
    # 3. EvaluationClient — per-session on-demand evaluation
    # ============================================================
    #
    # EvaluationClient.run() fetches the session's CloudWatch spans internally
    # and evaluates them — no manual log collection needed.
    # The SDK calls get_evaluator() to discover each evaluator's level
    # (SESSION / TRACE / TOOL_CALL) and routes results accordingly.

    print("\n[3/4] EvaluationClient + Strands.SkillInvoked — per-session on-demand evaluation ...")

    from bedrock_agentcore.evaluation import EvaluationClient

    ec = EvaluationClient(region_name=region)

    # The deterministic Strands SkillInvoked evaluator runs alongside the two
    # AgentCore built-ins below. Fail fast with an install hint if its package is
    # missing rather than surfacing a bare ImportError mid-session.
    try:
        import strands_evals
    except ImportError:
        print(
            "ERROR: the SkillInvoked evaluator needs the strands-agents-evals package. "
            "Install it with: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    all_ec_results: dict[str, Any] = {}
    failures: list[str] = []

    for session in sessions:
        end_time = datetime.now(timezone.utc)
        print(f"\n  --- {session['name']} ---")

        # Query the unified runtime log group once and pull the skills-span signal
        # for every session. The verbose attribute dump runs only for the PTO/custom
        # session (to keep output readable), but each session needs invoked_skill and
        # skill_content to rebuild the Strands SkillInvoked trajectory below.
        #
        # SkillSelectionAccuracy  reads: invoked_skill, available_skills,
        #                                user_message, context
        # SkillInstructionFollowing reads: invoked_skill, skill_content, context
        span_attrs: dict[str, Any] = {}
        skill_signals: dict[str, Any] = {}
        verbose_session = session["name"] in ("pto-planning", "custom-prompt")
        try:
            records = _query_session_spans(
                logs_client,
                config["cw_log_group"],
                session["session_id"],
                session["start"],
                end_time,
            )
            skill_signals = _extract_skill_signals(records)
            if verbose_session:
                print("\n  Extracting skill span attributes (evaluator inputs) ...")
                print(f"  {len(records)} span records found for session {session['session_id']}")
                available_skills = _read_available_skills(Path(__file__).parent / "skills")
                span_attrs = _print_skill_span_attributes(records, available_skills)
                print()
        except Exception as exc:  # noqa: BLE001
            print(f"  Could not fetch session spans: {exc}")

        results_by_evaluator: dict[str, list] = {}
        for evaluator_id in _EVALUATOR_IDS:
            results = ec.run(
                evaluator_ids=[evaluator_id],
                session_id=session["session_id"],
                agent_id=config["agent_id"],
                look_back_time=timedelta(hours=2),
            )
            results_by_evaluator[evaluator_id] = results
            _print_eval_results(session["name"], evaluator_id, results)

            expected_count = 1 if session["expected_skill"] else 0
            if len(results) != expected_count:
                failures.append(
                    f"{session['name']} / {evaluator_id}: expected {expected_count} result(s), got {len(results)}"
                )
            for result in results:
                if result.get("errorCode"):
                    failures.append(
                        f"{session['name']} / {evaluator_id}: {result['errorCode']} - {result.get('errorMessage', '')}"
                    )

        # Strands SkillInvoked (deterministic, client-side) — scores every session,
        # including the no-skill control, which the two LLM built-ins skip.
        skill_invoked = _evaluate_skill_invoked(session, skill_signals, config.get("skills", []), failures)

        all_ec_results[session["name"]] = {
            "session_id": session["session_id"],
            "prompt": session["prompt"],
            "expected_skill": session["expected_skill"],
            "response": session["response"],
            "span_attributes": span_attrs,
            "evaluations": results_by_evaluator,
            "skill_invoked": skill_invoked,
        }

    _ec_path = _RESULTS_DIR / "eval_client_results.json"
    _ec_path.write_text(json.dumps(all_ec_results, indent=2, default=str))
    print(f"\n  EvaluationClient results saved to: {_ec_path}")

    # Skip batch evaluation for single custom-prompt runs.
    if args.prompt:
        if failures:
            print("\nValidation failed:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        print("\nValidation passed.")
        return 0

    # ============================================================
    # 4. BatchEvaluationRunner — aggregate scores across scenarios
    # ============================================================
    #
    # BatchEvaluationRunner invokes the agent for each scenario in a dataset,
    # waits for CloudWatch ingestion, then submits all sessions as a single
    # service-side batch job and returns aggregate scores per evaluator.
    # Uses the unified runtime log group (single log group, July 2026 update).

    print("\n[4/4] BatchEvaluationRunner — aggregate scores across all scenarios ...")

    from bedrock_agentcore.evaluation import (
        AgentInvokerInput,
        AgentInvokerOutput,
        Dataset,
        PredefinedScenario,
        Turn,
    )
    from bedrock_agentcore.evaluation.runner.batch.batch_evaluation_models import (
        BatchEvaluationRunConfig,
        BatchEvaluatorConfig,
        CloudWatchDataSourceConfig,
    )
    from bedrock_agentcore.evaluation.runner.batch.batch_evaluation_runner import (
        BatchEvaluationRunner,
    )

    def _agent_invoker(invoker_input: AgentInvokerInput) -> AgentInvokerOutput:
        payload = invoker_input.payload
        body = {"prompt": payload} if isinstance(payload, str) else payload
        resp = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=config["agent_arn"],
            qualifier="DEFAULT",
            runtimeSessionId=invoker_input.session_id,
            payload=json.dumps(body).encode("utf-8"),
        )
        raw = resp["response"].read().decode("utf-8")
        parts = []
        for line in raw.splitlines():
            if line.startswith("data: "):
                chunk = line[len("data: ") :]
                try:
                    chunk = json.loads(chunk)
                except Exception:  # noqa: BLE001, S110
                    pass
                parts.append(str(chunk))
        return AgentInvokerOutput(agent_output="".join(parts) if parts else raw)

    # Skill evaluators require no ground truth — scenarios just carry prompts.
    batch_dataset = Dataset(
        scenarios=[
            PredefinedScenario(
                scenario_id=s["name"],
                turns=[Turn(input=s["prompt"])],
            )
            for s in _SCENARIOS
        ]
    )

    # Unified telemetry: all agent spans land in the single runtime log group.
    batch_data_source = CloudWatchDataSourceConfig(
        service_names=[config["otel_service_name"]],
        log_group_names=[config["cw_log_group"]],
        ingestion_delay_seconds=args.wait,
    )

    batch_config = BatchEvaluationRunConfig(
        batch_evaluation_name=f"skill_eval_{uuid.uuid4().hex[:8]}",
        evaluator_config=BatchEvaluatorConfig(evaluator_ids=list(_EVALUATOR_IDS)),
        data_source=batch_data_source,
        polling_timeout_seconds=1800,
        polling_interval_seconds=30,
    )

    print(f"  Batch name : {batch_config.batch_evaluation_name}")
    print(f"  Evaluators : {list(_EVALUATOR_IDS)}")
    print(f"  Scenarios  : {len(batch_dataset.scenarios)}")
    print("  Invoking agent + submitting batch (includes ingestion wait) ...")

    batch_runner = BatchEvaluationRunner(region=region)
    batch_result = batch_runner.run_dataset_evaluation(
        config=batch_config,
        dataset=batch_dataset,
        agent_invoker=_agent_invoker,
    )

    print(f"\n  Batch ID : {batch_result.batch_evaluation_id}")
    print(f"  Status   : {batch_result.status}")

    _batch_data: dict[str, Any] = {
        "batch_evaluation_id": batch_result.batch_evaluation_id,
        "status": batch_result.status,
    }

    if batch_result.evaluation_results:
        ev = batch_result.evaluation_results
        print(f"  Sessions : {ev.number_of_sessions_completed} completed, {ev.number_of_sessions_failed} failed")
        if ev.evaluator_summaries:
            print("\n  Aggregate scores per evaluator:")
            print(f"  {'Evaluator':<40} {'avg score':>10}  n")
            print(f"  {'-' * 40} {'-' * 10}  -")
            summaries = []
            for es in ev.evaluator_summaries:
                score = (
                    f"{es.statistics.average_score:.3f}"
                    if es.statistics and es.statistics.average_score is not None
                    else "N/A"
                )
                print(f"  {(es.evaluator_id or ''):<40} {score:>10}  {es.total_evaluated or 0}")
                summaries.append(
                    {
                        "evaluator_id": es.evaluator_id,
                        "average_score": es.statistics.average_score if es.statistics else None,
                        "total_evaluated": es.total_evaluated,
                    }
                )
            _batch_data["evaluator_summaries"] = summaries
            _batch_data["sessions_completed"] = ev.number_of_sessions_completed
            _batch_data["sessions_failed"] = ev.number_of_sessions_failed

    _br_path = _RESULTS_DIR / "batch_runner_results.json"
    _br_path.write_text(json.dumps(_batch_data, indent=2, default=str))
    print(f"\n  BatchRunner results saved to: {_br_path}")

    # Validate batch result. The no-skill-control scenario has no skill invocation so
    # the skill evaluators cannot score it — that counts as exactly 1 expected failure.
    expected_batch_failures = sum(1 for s in _SCENARIOS if not s["expected_skill"])
    if batch_result.evaluation_results:
        actual_failed = batch_result.evaluation_results.number_of_sessions_failed or 0
        if actual_failed > expected_batch_failures:
            failures.append(f"Batch: {actual_failed} sessions failed (expected at most {expected_batch_failures})")

    # ============================================================
    # Summary
    # ============================================================

    print("\n" + "=" * 88)
    print("Summary")
    print("=" * 88)
    print(f"  EvaluationClient results : {_ec_path}")
    print(f"  BatchRunner results      : {_br_path}")
    print()
    print("  Interface comparison:")
    print("    EvaluationClient      → per-session, on-demand, synchronous")
    print("    BatchEvaluationRunner → dataset-level, service-side, aggregate scores")

    if failures:
        print("\nValidation failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("\nValidation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
