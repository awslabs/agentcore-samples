"""Finance reports: progress notifications, cancellation, and background work with HealthyBusy.

A request that carries a progressToken gets notifications/progress on its response stream. In MCP
2026-07-28 a client cancels a request by closing that stream, and the SDK then cancels the tool. Through
AgentCore Runtime today the disconnect doesn't reach the server, so work that may need stopping runs as a
background job with its own cancel tool. While a job runs, GET /ping reports HealthyBusy, which keeps the
microVM alive.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Annotated, Literal

import anyio
from dynamo_utils import FinanceDB
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field
from runtime_utils import add_ping, health, instance_id, serve

logger = logging.getLogger("finance-reports")
mcp = MCPServer(name="finance-reports")
add_ping(mcp)
db = FinanceDB()

STEPS = ["Reading transactions", "Totalling by category", "Reading budgets", "Comparing with budgets", "Formatting"]


class Report(BaseModel):
    report: str
    instance_id: str  # which microVM built it


class ReportJob(BaseModel):
    job_id: str
    status: Literal["running", "done", "cancelled"]
    ping_status: str  # what GET /ping reports right now
    instance_id: str


class ServerStatus(BaseModel):
    instance_id: str
    ping_status: str
    running_jobs: int
    last_report: str  # "none", "running", "completed", or "cancelled/failed during step N of 5"


# Per-microVM state. AgentCore Runtime sends every request of a runtime session to the same microVM.
last_report = {"status": "none"}
jobs: dict[str, str] = {}
job_tasks: dict[str, asyncio.Task[None]] = {}


# ------------------------------------------------------------------- progress and cancellation
@mcp.tool()
async def generate_report(
    user_alias: str,
    ctx: Context,
    step_seconds: Annotated[float, Field(ge=0, le=30, description="Pause after each step")] = 1.0,
) -> Report:
    """Build a monthly spending report in five steps, with a progress notification as each step starts."""
    # An async tool runs on the event loop, which also answers GET /ping, so the blocking DynamoDB calls go to a
    # worker thread. (The SDK already runs sync tools in a thread.)
    step = 0

    async def start(name: str) -> None:
        nonlocal step
        step += 1
        last_report["status"] = "running"
        await ctx.report_progress(step, len(STEPS), name)
        await anyio.sleep(step_seconds)  # stands in for slower work

    try:
        await start(STEPS[0])
        transactions = await anyio.to_thread.run_sync(db.get_transactions, user_alias)
        await start(STEPS[1])
        spent: dict[str, float] = {}
        for t in transactions:
            if t["type"] == "expense":
                spent[t["category"]] = spent.get(t["category"], 0.0) - float(t["amount"])
        await start(STEPS[2])
        budgets = {
            b["category"]: float(b["monthly_limit"]) for b in await anyio.to_thread.run_sync(db.get_budgets, user_alias)
        }
        await start(STEPS[3])
        lines = []
        for category, amount in sorted(spent.items(), key=lambda item: -item[1]):
            limit = budgets.get(category)
            if limit:
                status = "OVER" if amount > limit else "OK"
                lines.append(f"{category:<15} ${amount:>8.2f} / ${limit:.2f}  [{amount / limit:.0%}] {status}")
            else:
                lines.append(f"{category:<15} ${amount:>8.2f}  (no budget set)")
        await start(STEPS[4])
        report = "\n".join([f"Monthly report for {user_alias}", *lines, f"{'TOTAL':<15} ${sum(spent.values()):>8.2f}"])
    except anyio.get_cancelled_exc_class():
        # The client closed the response stream. Record where the work stopped, then let the cancellation through.
        last_report["status"] = f"cancelled during step {step} of {len(STEPS)}"
        logger.info("generate_report for %s %s", user_alias, last_report["status"])
        raise
    except Exception:
        last_report["status"] = f"failed during step {step} of {len(STEPS)}"
        logger.exception("generate_report for %s %s", user_alias, last_report["status"])
        raise
    last_report["status"] = "completed"
    return Report(report=report, instance_id=instance_id())


# ------------------------------------------------------------------- background work and HealthyBusy
async def run_job(job_id: str, seconds: int) -> None:
    try:
        await asyncio.sleep(seconds)  # stands in for a long report
        jobs[job_id] = "done"
    except asyncio.CancelledError:
        jobs[job_id] = "cancelled"
        logger.info("report job %s cancelled", job_id)
    finally:
        job_tasks.pop(job_id, None)
        health.job_finished()


@mcp.tool()
async def start_report_job(seconds: Annotated[int, Field(ge=1, le=3600)] = 60) -> ReportJob:
    """Start a report in the background and return at once. While it runs, GET /ping reports HealthyBusy."""
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    jobs[job_id] = "running"
    health.job_started()
    job_tasks[job_id] = asyncio.create_task(run_job(job_id, seconds))
    return ReportJob(job_id=job_id, status="running", ping_status=health.status, instance_id=instance_id())


def find_job(job_id: str) -> str:
    if job_id not in jobs:
        raise ToolError(f"Unknown job {job_id} on instance {instance_id()}: was the runtime session the same?")
    return jobs[job_id]


@mcp.tool()
def get_report_job(job_id: str) -> ReportJob:
    """Return a background job's status. Jobs live in one microVM's memory, so use the same runtime session."""
    return ReportJob(job_id=job_id, status=find_job(job_id), ping_status=health.status, instance_id=instance_id())


@mcp.tool()
async def cancel_report_job(job_id: str) -> ReportJob:
    """Stop a background job. Unlike closing a response stream, this works through AgentCore Runtime."""
    find_job(job_id)
    task = job_tasks.get(job_id)
    if task:
        task.cancel()
        await asyncio.wait([task])  # let it record the cancellation and update GET /ping
    return ReportJob(job_id=job_id, status=jobs[job_id], ping_status=health.status, instance_id=instance_id())


@mcp.tool()
def server_status() -> ServerStatus:
    """Report which microVM answered, what GET /ping reports, and how the last report ended."""
    running = sum(status == "running" for status in jobs.values())
    return ServerStatus(
        instance_id=instance_id(), ping_status=health.status, running_jobs=running, last_report=last_report["status"]
    )


if __name__ == "__main__":
    serve(mcp)
