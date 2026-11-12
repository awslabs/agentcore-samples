"""strands-decider on AgentCore Runtime, with a build warmup hook.

The platform calls POST /warmup while it builds the runtime:
  memory  build VM, before the snapshot: waits until the model is loaded and
          its weights are copied into process memory, so the snapshot taken
          right after holds a fully loaded model.
  disk    validate VM: reads the native libraries inference loads (torch and
          friends), the files that otherwise read cold from the image on the
          first request.

The model also loads in a background thread at import, so /ping answers
right away and the agent still works if the hook isn't called.
"""

import json
import os
import threading
import time

from starlette.requests import Request
from starlette.responses import JSONResponse

from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Downloaded at a pinned revision by the Dockerfile; HF_HUB_OFFLINE=1 keeps
# the runtime from contacting the Hugging Face Hub.
CHECKPOINT = "/app/model"
SITE_PACKAGES = "/usr/local/lib/python3.11/site-packages"
# The hook's 120 s budget is shared by both calls; any timeout fails the build.
# The load takes ~5 s on the build VM, so both caps leave a wide margin.
MEMORY_WAIT_S = 50
DISK_BUDGET_S = 50

app = BedrockAgentCoreApp()

_loaded = threading.Event()
_engine = None
_question_types = {}
_load_error = None
_upcast = None
_upcast_lock = threading.Lock()
_hooks_seen: list[str] = []


def _rss_mib() -> int:
    with open("/proc/self/status") as f:
        return next(int(line.split()[1]) // 1024 for line in f if line.startswith("VmRSS"))


def _load():
    """Load in bf16 and copy the weights out of the safetensors mmap.

    load_engine only maps the weight files, and the page cache is dropped
    before the snapshot: left mapped, the first request reads ~4 GiB cold
    from the image. The library's fp32 upcast (~7.5 GiB) is deferred to the
    first request; a snapshot that size failed to build.
    """
    global _engine, _question_types, _load_error, _upcast
    try:
        import torch
        from strands_decider.infer import SystemOneEngine, load_engine
        from strands_decider.schema import ChoiceQuestion, NoulQuestion, ScoreQuestion

        _upcast = SystemOneEngine._upcast_torso_for_cpu
        SystemOneEngine._upcast_torso_for_cpu = lambda self: None
        _question_types = {"noul": NoulQuestion, "choice": ChoiceQuestion, "score": ScoreQuestion}
        engine = load_engine(CHECKPOINT, device="cpu")
        with torch.inference_mode():
            engine.model._apply(lambda t: t.clone())
            # HostEmbedding's _apply only honors dtype changes, so a clone skips it.
            for module in engine.model.modules():
                if type(module).__name__ == "HostEmbedding":
                    module.inner.weight.data = module.inner.weight.data.clone()
        _engine = engine
        print(f"decider engine loaded (bf16 in memory, rss={_rss_mib()} MiB)", flush=True)
    except Exception as exc:  # noqa: BLE001 - reported by the hook and every invoke
        _load_error = exc
        print(f"decider engine failed to load: {exc!r}", flush=True)
    finally:
        _loaded.set()


threading.Thread(target=_load, daemon=True).start()


def _native_libraries() -> list[str]:
    """Shared libraries under site-packages, largest first."""
    paths = []
    for root, _, files in os.walk(SITE_PACKAGES):
        paths += [os.path.join(root, f) for f in files if ".so" in f]
    return sorted(paths, key=os.path.getsize, reverse=True)


def _read_files(paths: list[str], budget_s: float) -> dict:
    start, read, files = time.monotonic(), 0, 0
    for path in paths:
        if time.monotonic() - start > budget_s:
            break
        with open(path, "rb", buffering=0) as f:
            while chunk := f.read(8 << 20):
                read += len(chunk)
        files += 1
    return {"files": files, "of_files": len(paths), "read_mib": read >> 20}


def warmup(request: Request) -> JSONResponse:
    kind = request.headers.get("X-Amzn-Bedrock-AgentCore-Runtime-Warmup-Type")
    start = time.monotonic()
    if kind == "memory":
        if not _loaded.wait(MEMORY_WAIT_S) or _load_error is not None:
            reason = repr(_load_error) if _load_error else f"model not loaded after {MEMORY_WAIT_S}s"
            print(json.dumps({"event": "warmup", "type": kind, "error": reason}), flush=True)
            return JSONResponse({"error": reason}, status_code=500)  # fails the build, visibly
        stats = {"rss_mib": _rss_mib()}
    elif kind == "disk":
        stats = _read_files(_native_libraries(), DISK_BUDGET_S)
    else:
        return JSONResponse({"error": f"unknown warmup type {kind!r}"}, status_code=400)
    _hooks_seen.append(kind)
    stats.update(event="warmup", type=kind, seconds=round(time.monotonic() - start, 1))
    print(json.dumps(stats), flush=True)
    return JSONResponse(stats)


# Sync endpoint: Starlette runs it in a threadpool, so the hook never blocks /ping.
app.add_route("/warmup", warmup, methods=["POST"])


def _upcast_once():
    """Convert the torso to fp32 on the first invocation, i.e. after the restore.

    The CPU AgentCore Runtime ran on has no native bf16, so bf16 inference is
    emulated and too slow; fp32 is fast once the weights are in memory.
    """
    global _upcast
    if _upcast is None:
        return
    with _upcast_lock:
        if _upcast is not None:
            start = time.monotonic()
            _upcast(_engine)
            _upcast = None
            print(f"decider torso upcast to fp32 in {time.monotonic() - start:.1f}s", flush=True)


def _build_question(spec: dict):
    cls = _question_types.get(spec.get("type"))
    if cls is None:
        raise ValueError(f"unknown question type {spec.get('type')!r}, expected one of {list(_question_types)}")
    return cls(**{k: v for k, v in spec.items() if k != "type"})


@app.entrypoint
def invoke(payload=None):
    payload = payload or {}
    raw_questions = payload.get("questions") or {}
    if not raw_questions:
        return {"error": "payload must include at least one entry in 'questions'"}

    _loaded.wait()
    if _load_error is not None:
        return {"error": f"model failed to load: {_load_error!r}"}
    _upcast_once()

    try:
        questions = {name: _build_question(spec) for name, spec in raw_questions.items()}
    except Exception as exc:  # noqa: BLE001 - surface a bad payload plainly, not a 500
        return {"error": f"invalid question: {exc}"}

    response = _engine.ask(payload.get("state", ""), questions).model_dump()
    response["hooks_seen"] = _hooks_seen
    return response


if __name__ == "__main__":
    app.run()
