"""Mastering agent.

Reads the audio the composition agent rendered onto the shared volume, has a
model choose a mastering chain from real measurements of that audio, applies the
chain with real DSP, and measures the result.

The division of labour is the point. The model decides *what* to do -- which
bands to move, how hard to compress, what to leave alone -- and the DSP in
audio_dsp.py does it deterministically. Nothing here is a plan the model
merely asserts: the output file is measured after processing, and the compliance
agent measures it again independently.

Packaged as a container image in Amazon ECR. Runs on the same GPU instance as
the composition agent because collocation is what gives it access to the audio,
but it does no GPU work of its own -- mastering is filters and gain, and putting
it on the CPU keeps the GPU free for generation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import uuid
from pathlib import Path

import audio_dsp as audio
import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import BaseModel, Field, field_validator
from strands import Agent
from strands.models import BedrockModel
from strands.session import FileSessionManager

AGENT_NAME = "mastering"
PROCESS_ID = uuid.uuid4().hex[:8]

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/mnt/tracks"))
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
ARTIFACT_BUCKET = os.environ.get("ARTIFACT_BUCKET")

# Streaming loudness targets. Platforms normalise on playback, so mastering
# louder than the target buys nothing and costs dynamic range.
PLATFORM_TARGETS = {
    "spotify": (-14.0, -1.0),
    "apple": (-16.0, -1.0),
    "youtube": (-14.0, -1.0),
    "amazon": (-14.0, -2.0),
    "broadcast": (-23.0, -1.0),
}

SYSTEM_PROMPT = """You are a mastering engineer.

You will be given measurements of a rendered mix and a delivery target. Return a
mastering chain as structured data. Be conservative and specific:

- Only include EQ bands that address something visible in the measurements.
  Three or four bands is a normal master; twelve is not.
- Corrective moves are small. Use gains between -4 and +4 dB unless the
  measurements justify more.
- If the mix already has healthy dynamics, compress gently or not at all. Say so
  in `left_alone` rather than adding processing to look busy.
- Loudness is reached by a single broadband gain after your chain, not by
  slamming the limiter. The limiter is there to catch peaks, so expect only a
  decibel or two of gain reduction from it.
- `notes` should explain your reasoning in two or three sentences, referring to
  the measured numbers you are responding to."""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(AGENT_NAME)

app = BedrockAgentCoreApp()


class WorkflowError(Exception):
    """A recoverable problem in the pipeline, reported to the caller as data."""


class EqBand(BaseModel):
    type: str = Field(description="highpass, lowpass, peaking, lowshelf or highshelf")
    freq_hz: float = Field(description="Centre or corner frequency in Hz, 20-20000.")
    gain_db: float = Field(default=0.0, description="Cut or boost in dB. Ignored for highpass/lowpass.")
    q: float = Field(default=0.707, description="Filter Q, typically 0.5-2.0.")
    reason: str = Field(default="", description="What measurement this band addresses.")

    @field_validator("type", mode="before")
    @classmethod
    def _normalise_type(cls, v):
        # Models write these a dozen ways: "high-pass", "High Shelf", "HPF".
        if not isinstance(v, str):
            return "peaking"
        t = v.strip().lower().replace("-", "").replace("_", "").replace(" ", "")
        return {
            "hpf": "highpass",
            "lpf": "lowpass",
            "bell": "peaking",
            "shelf": "highshelf",
            "lowshelving": "lowshelf",
            "highshelving": "highshelf",
        }.get(t, t)

    @field_validator("freq_hz")
    @classmethod
    def _clamp_freq(cls, v):
        return float(min(max(v, 20.0), 20000.0))

    @field_validator("gain_db")
    @classmethod
    def _clamp_gain(cls, v):
        # A model occasionally asks for +18 dB. Refuse politely rather than
        # destroy the master.
        return float(min(max(v, -12.0), 12.0))


class Compressor(BaseModel):
    enabled: bool = Field(default=True)
    threshold_db: float = Field(default=-18.0)
    ratio: float = Field(default=2.0)
    attack_ms: float = Field(default=20.0)
    release_ms: float = Field(default=200.0)
    knee_db: float = Field(default=6.0)

    @field_validator("ratio")
    @classmethod
    def _clamp_ratio(cls, v):
        return float(min(max(v, 1.0), 20.0))


class MasteringPlan(BaseModel):
    eq_bands: list[EqBand] = Field(default_factory=list)
    compressor: Compressor = Field(default_factory=Compressor)
    target_lufs: float = Field(default=-14.0)
    target_true_peak_dbtp: float = Field(default=-1.0)
    notes: str = Field(default="")
    left_alone: str = Field(default="", description="What you deliberately did not touch, and why.")

    @field_validator("eq_bands", mode="before")
    @classmethod
    def _coerce_bands(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                return []
        return v if isinstance(v, list) else []

    @field_validator("target_true_peak_dbtp")
    @classmethod
    def _sane_ceiling(cls, v):
        return float(min(max(v, -6.0), -0.1))


# --------------------------------------------------------------------- workspace


def track_path(track_id: str) -> Path:
    safe = "".join(c for c in track_id if c.isalnum() or c in "-_")[:64] or "track"
    return WORKSPACE / safe


def require_track_dir(track_id: str) -> Path:
    """Fail loudly if the composition agent has not created the track yet.

    Deliberately does not create it: a container agent and a zip agent run as
    different identities, so whichever creates a directory first can lock the
    other out. Creation belongs to the composition agent alone.
    """
    path = track_path(track_id)
    if not path.is_dir():
        raise WorkflowError(
            f"No workspace for track '{track_id}'. Invoke the composition agent "
            "first, with the same runtimeSessionId so both agents land on the "
            "same instance and see the same volume."
        )
    return path


def read_text(track_id: str, name: str) -> str | None:
    p = track_path(track_id) / name
    return p.read_text(encoding="utf-8") if p.exists() else None


def write_text(track_id: str, name: str, text: str) -> Path:
    p = track_path(track_id) / name
    p.write_text(text, encoding="utf-8")
    try:
        p.chmod(0o664)
    except PermissionError:
        pass
    logger.info("wrote %s (%d bytes)", p, len(text))
    return p


def latest_render(track_id: str) -> tuple[str, Path]:
    """Prefer a remediated render over the original.

    Same precedence the compliance agent uses, so a rerun masters the
    replacement rather than re-mastering material that has been superseded.
    """
    for name in ("composition_remediated.wav", "composition.wav"):
        p = track_path(track_id) / name
        if p.exists() and p.stat().st_size > 0:
            return name, p
    raise WorkflowError(
        "No rendered audio in the shared workspace. Invoke the composition agent first, with the same runtimeSessionId."
    )


def list_artifacts(track_id: str) -> list[str]:
    p = track_path(track_id)
    return sorted(x.name for x in p.iterdir() if x.is_file()) if p.is_dir() else []


def publish(track_id: str, path: Path) -> dict:
    """Copy an artifact to S3 and hand back a link the caller can actually open.

    The volume lives inside a managed instance with no shell and is destroyed
    with the session, so S3 is the only route by which a 30 MB WAV reaches
    whoever invoked us.
    """
    info: dict = {
        "name": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
    }
    if not ARTIFACT_BUCKET:
        return info
    key = f"tracks/{track_id}/{path.name}"
    # The regional endpoint is explicit on purpose. Left to itself botocore
    # presigned against the global host (bucket.s3.amazonaws.com) while scoping
    # the signature to us-east-2, and every URL came back 403
    # SignatureDoesNotMatch. Measured on a live run.
    s3 = boto3.client("s3", region_name=REGION, endpoint_url=f"https://s3.{REGION}.amazonaws.com")
    s3.upload_file(str(path), ARTIFACT_BUCKET, key)
    info["s3_uri"] = f"s3://{ARTIFACT_BUCKET}/{key}"
    info["url"] = s3.generate_presigned_url(
        "get_object", Params={"Bucket": ARTIFACT_BUCKET, "Key": key}, ExpiresIn=86400
    )
    logger.info("published %s", info["s3_uri"])
    return info


def host_info() -> dict:
    """Facts that make collocation observable from the response."""
    u = platform.uname()
    return {"process_id": PROCESS_ID, "hostname": u.node, "architecture": u.machine, "cpus": os.cpu_count()}


# ------------------------------------------------------------------------ agent


def build_agent(session_id: str, track_id: str) -> Agent:
    """Construct a fresh Agent per invocation.

    A module-level Agent is shared by every concurrent request and Strands
    rejects re-entrant invocation, so this is a correctness requirement rather
    than a style choice. Conversation history lives on the volume instead.
    """
    kwargs: dict = {"model_id": MODEL_ID}
    if REGION:
        kwargs["region_name"] = REGION
    return Agent(
        name=AGENT_NAME,
        model=BedrockModel(**kwargs),
        system_prompt=SYSTEM_PROMPT,
        session_manager=FileSessionManager(
            session_id=f"{session_id}-{AGENT_NAME}",
            # Per-agent: FileSessionManager creates its storage directory with
            # mode 0700, which locks out an agent running as another identity.
            storage_dir=str(track_path(track_id) / f".sessions-{AGENT_NAME}"),
        ),
    )


def apply_chain(src: Path, dst: Path, plan: MasteringPlan) -> dict:
    """Run the model's chain and measure what actually came out.

    Order matters and is fixed here rather than left to the model: tonal shaping,
    then dynamics, then loudness, then a true-peak safety limiter last. Reaching
    loudness before limiting means the limiter only catches transients instead of
    doing the level-setting.
    """
    data, rate = audio.read_audio(str(src))
    before = audio.measure(str(src))
    steps: list[dict] = []

    bands = [b.model_dump() for b in plan.eq_bands]
    if bands:
        data = audio.apply_filters(data, rate, bands)
        steps.append({"stage": "eq", "bands": bands})

    if plan.compressor.enabled:
        data, comp_info = audio.compress(
            data,
            rate,
            threshold_db=plan.compressor.threshold_db,
            ratio=plan.compressor.ratio,
            attack_ms=plan.compressor.attack_ms,
            release_ms=plan.compressor.release_ms,
            knee_db=plan.compressor.knee_db,
        )
        steps.append({"stage": "compressor", **plan.compressor.model_dump(), **comp_info})

    data, norm_info = audio.normalise_loudness(data, rate, plan.target_lufs)
    steps.append({"stage": "loudness", "target_lufs": plan.target_lufs, **norm_info})

    data, lim_info = audio.limit(data, rate, ceiling_dbtp=plan.target_true_peak_dbtp)
    steps.append({"stage": "limiter", "ceiling_dbtp": plan.target_true_peak_dbtp, **lim_info})

    audio.write_audio(str(dst), data, rate, subtype="PCM_24")
    after = audio.measure(str(dst))

    # The reason this agent is worth deploying: it checks its own homework.
    lufs_err = abs(after.integrated_lufs - plan.target_lufs) if after.integrated_lufs is not None else None
    return {
        "before": before.to_dict(),
        "after": after.to_dict(),
        "steps": steps,
        "targets": {"lufs": plan.target_lufs, "true_peak_dbtp": plan.target_true_peak_dbtp},
        "hit_loudness_target": bool(lufs_err is not None and lufs_err <= 0.5),
        "hit_peak_target": bool(
            after.true_peak_dbtp is not None and after.true_peak_dbtp <= plan.target_true_peak_dbtp + 0.05
        ),
        "loudness_error_lu": round(lufs_err, 2) if lufs_err is not None else None,
    }


def render_report(plan: MasteringPlan, result: dict, source_name: str) -> str:
    b, a = result["before"], result["after"]

    def row(label: str, key: str, unit: str) -> str:
        bv, av = b.get(key), a.get(key)
        fmt = lambda v: "-inf" if v is None else f"{v:g}"
        return f"| {label} | {fmt(bv)}{unit} | {fmt(av)}{unit} |"

    lines = [
        "# Mastering Report",
        "",
        (f"**Source:** `{source_name}`  |  **Target:** {plan.target_lufs} LUFS / {plan.target_true_peak_dbtp} dBTP"),
        "",
        "## Measured",
        "",
        "| | before | after |",
        "|---|---|---|",
        row("Integrated loudness", "integrated_lufs", " LUFS"),
        row("Loudness range", "loudness_range_lu", " LU"),
        row("True peak", "true_peak_dbtp", " dBTP"),
        row("Sample peak", "sample_peak_dbfs", " dBFS"),
        row("Stereo correlation", "stereo_correlation", ""),
        row("Clipped runs", "clipped_runs", ""),
        "",
        (
            f"Loudness target {'met' if result['hit_loudness_target'] else 'MISSED'} "
            f"(error {result['loudness_error_lu']} LU). "
            f"True-peak ceiling {'held' if result['hit_peak_target'] else 'EXCEEDED'}."
        ),
        "",
        "## Chain",
        "",
    ]
    for band in plan.eq_bands:
        lines.append(f"- **EQ** {band.type} @ {band.freq_hz:g} Hz, {band.gain_db:+g} dB, Q {band.q:g} — {band.reason}")
    c = plan.compressor
    lines.append(
        f"- **Compressor** {'bypassed' if not c.enabled else f'{c.ratio:g}:1 @ {c.threshold_db:g} dB, attack {c.attack_ms:g} ms, release {c.release_ms:g} ms'}"
    )
    for step in result["steps"]:
        if step["stage"] == "loudness":
            lines.append(f"- **Loudness** {step['applied_gain_db']:+g} dB broadband")
        if step["stage"] == "limiter":
            lines.append(
                f"- **Limiter** ceiling {step['ceiling_dbtp']:g} dBTP, "
                f"max {step['max_gain_reduction_db']:g} dB reduction"
            )
    lines += [
        "",
        "## Engineer's notes",
        "",
        plan.notes or "_none_",
        "",
        "## Deliberately left alone",
        "",
        plan.left_alone or "_nothing noted_",
    ]
    return "\n".join(lines)


@app.entrypoint
def invoke(payload, context):
    """AgentCore POSTs the invocation payload here.

    The second parameter must be named exactly ``context`` for the SDK to pass
    the request context, which is the only way to read the session id.
    """
    session_id = getattr(context, "session_id", None) or "local-session"
    track_id = payload.get("track_id", "demo-track")
    platform_name = str(payload.get("platform", "spotify")).lower()
    prompt = payload.get("prompt") or f"Master this for {platform_name}."
    if not isinstance(prompt, str):
        # The payload is arbitrary JSON. A non-string here can carry toolUse
        # content blocks straight into the framework's event loop.
        return {"status": "error", "agent": AGENT_NAME, "error": "'prompt' must be a string", "host": host_info()}

    target_lufs, target_peak = PLATFORM_TARGETS.get(platform_name, PLATFORM_TARGETS["spotify"])
    logger.info("invoke: track=%s session=%s platform=%s model=%s", track_id, session_id, platform_name, MODEL_ID)

    try:
        require_track_dir(track_id)
        source_name, source = latest_render(track_id)
        brief = read_text(track_id, "composition_remediated.md") or read_text(track_id, "composition.md")
        source_measurements = audio.measure(str(source))
        logger.info("source %s: %s", source_name, source_measurements.to_dict())

        task = (
            f"Mix to master (from {source_name}, rendered by the composition agent "
            f"on this instance).\n\n"
            f"Measured properties of the mix:\n{json.dumps(source_measurements.to_dict(), indent=2)}\n\n"
            + (f"Composition brief:\n{brief}\n\n" if brief else "")
            + f"Delivery target: {platform_name} at {target_lufs} LUFS integrated, "
            f"true peak at or below {target_peak} dBTP.\n\n"
            f"Request: {prompt}"
        )

        agent = build_agent(session_id, track_id)
        result = agent(task, structured_output_model=MasteringPlan)
        plan: MasteringPlan = result.structured_output or MasteringPlan(
            target_lufs=target_lufs, target_true_peak_dbtp=target_peak
        )
        # The platform target is not the model's to override.
        plan.target_lufs = target_lufs
        plan.target_true_peak_dbtp = target_peak

        master = track_path(track_id) / "master.wav"
        applied = apply_chain(source, master, plan)
        try:
            master.chmod(0o664)
        except PermissionError:
            pass

        report = render_report(plan, applied, source_name)
        write_text(track_id, "mastering.md", report)
        write_text(
            track_id,
            "mastering.json",
            json.dumps(
                {"plan": plan.model_dump(), "result": applied, "source": source_name, "platform": platform_name},
                indent=2,
            ),
        )

        artifacts = [publish(track_id, master), publish(track_id, track_path(track_id) / "mastering.md")]

        return {
            "status": "ok",
            "agent": AGENT_NAME,
            "model": MODEL_ID,
            "track_id": track_id,
            "session_id": session_id,
            "read_from": source_name,
            "platform": platform_name,
            "measurements": {"before": applied["before"], "after": applied["after"]},
            "targets_met": {"loudness": applied["hit_loudness_target"], "true_peak": applied["hit_peak_target"]},
            "plan": plan.model_dump(),
            "result": report,
            "artifacts": artifacts,
            "workspace_files": list_artifacts(track_id),
            "host": host_info(),
        }

    except WorkflowError as exc:
        # A pipeline-ordering problem is a real outcome, not a crash: report it
        # as data. Unexpected exceptions are left to propagate so the service
        # surfaces a 424 and the traceback reaches CloudWatch.
        logger.warning("workflow error: %s", exc)
        return {
            "status": "error",
            "agent": AGENT_NAME,
            "track_id": track_id,
            "session_id": session_id,
            "error": str(exc),
            "workspace_files": list_artifacts(track_id),
            "host": host_info(),
        }


if __name__ == "__main__":
    # Bind explicitly. app.run() with no host guesses 0.0.0.0 only when it finds
    # /.dockerenv or DOCKER_CONTAINER, neither of which exists under Finch or
    # containerd, and would otherwise bind 127.0.0.1 where the runtime cannot
    # reach it. AgentCore sets PORT, which run() does not consult.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
