"""FastAPI backend for the Refund Desk lifecycle hooks demo."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

import notifications
import tools
from agent import run_turn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from hooks import HOOK_DEFINITIONS, HOOKS_BY_NAME, apply_hooks, build_hooks
from pydantic import BaseModel
from resources import ensure_resources, get_chaos_mode, save_state, set_chaos_mode
from sse_starlette.sse import EventSourceResponse

_state: dict = {}
_hook_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _state
    print("[backend] Starting — provisioning AWS resources...", flush=True)
    _state = await asyncio.to_thread(ensure_resources)
    notifications.start(_state["queue_url"])
    print(f"[backend] Ready. Harness: {_state['harness_id']}", flush=True)
    yield
    print("[backend] Shutting down", flush=True)


app = FastAPI(title="Refund Desk — Harness Lifecycle Hooks", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    tamper: bool = False


class HookSetting(BaseModel):
    enabled: bool
    failureMode: str = "deny"


class HooksUpdate(BaseModel):
    settings: dict[str, HookSetting]


class ChaosUpdate(BaseModel):
    mode: str


@app.get("/health")
async def health():
    return {"status": "ok", "harness_id": _state.get("harness_id")}


@app.get("/api/status")
async def status():
    return {
        "ready": bool(_state.get("harness_id")),
        "harness_id": _state.get("harness_id"),
        "harness_name": _state.get("harness_name"),
        "region": _state.get("region"),
    }


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not _state.get("harness_arn"):
        raise HTTPException(503, "Resources not ready")
    if _hook_lock.locked():
        raise HTTPException(409, "Hooks are being updated — try again in a moment")

    # runtimeSessionId must be at least 33 characters.
    session_id = req.session_id or f"refund-desk-{uuid.uuid4()}"

    def generate():
        yield json.dumps({"type": "session_id", "session_id": session_id})
        try:
            for event in run_turn(
                _state["harness_arn"], session_id, req.message, _state["receipt_secret"], tamper=req.tamper
            ):
                yield json.dumps(event, default=str)
        except Exception as e:  # noqa: BLE001 — surface AWS errors in the chat
            yield json.dumps({"type": "error", "content": f"{type(e).__name__}: {e}"})
            yield json.dumps({"type": "done", "stop_reason": "error"})

    # sse-starlette runs a sync generator in a thread pool, so the blocking stream is fine here.
    return EventSourceResponse(generate())


@app.get("/api/hooks")
async def get_hooks():
    settings = _state.get("hook_settings", {})
    return {
        "hooks": [
            {
                **{k: h[k] for k in ("name", "event", "target", "description")},
                "timeout": h.get("timeout"),
                "arn": _state.get(h["arn_key"]),
                **settings.get(h["name"], {"enabled": True, "failureMode": "deny"}),
            }
            for h in HOOK_DEFINITIONS
        ],
        "config": build_hooks(_state, settings) if _state.get("harness_id") else [],
    }


@app.put("/api/hooks")
async def put_hooks(req: HooksUpdate):
    unknown = set(req.settings) - set(HOOKS_BY_NAME)
    if unknown:
        raise HTTPException(400, f"Unknown hooks: {sorted(unknown)}")
    settings = {name: s.model_dump() for name, s in req.settings.items()}
    async with _hook_lock:
        await asyncio.to_thread(apply_hooks, _state, settings)
        _state["hook_settings"] = settings
        save_state(_state)
    return await get_hooks()


@app.get("/api/chaos")
async def get_chaos():
    return {"mode": await asyncio.to_thread(get_chaos_mode, _state)}


@app.put("/api/chaos")
async def put_chaos(req: ChaosUpdate):
    if req.mode not in ("off", "slow", "error"):
        raise HTTPException(400, "mode must be off, slow or error")
    await asyncio.to_thread(set_chaos_mode, _state, req.mode)
    return {"mode": req.mode}


@app.get("/api/notifications")
async def get_notifications(since: int = 0):
    return notifications.since(since)


@app.get("/api/orders")
async def orders():
    return {"orders": tools.list_orders(), "outbox": tools.list_outbox()}


@app.post("/api/reset")
async def reset():
    tools.reset()
    return await orders()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
