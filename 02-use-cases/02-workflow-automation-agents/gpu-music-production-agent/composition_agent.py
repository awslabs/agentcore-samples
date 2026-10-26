"""Composition agent.

Turns a producer's request into an actual audio file, using a generative music
model running on the capacity provider instance's GPU. A Bedrock-hosted model
writes the brief and ACE-Step, running locally on the L4, renders it.

Be clear about how much of that brief reaches the audio: only ``style_tags`` and
``lyrics`` do. ACE-Step takes no other text input, so ``key``, ``tempo_bpm``,
``time_signature``, ``chord_progression``, ``instrumentation``, ``structure`` and
``title`` are documentation for composition.md and for the downstream agents to
read -- tempo influences the render only because the model writes "124 bpm" into
the tag string. The genuinely hard reasoning here is ``mode=remediate``, which has
to move a rejected piece decisively away from what it resembled.

That split is the reason this sample needs Runtime Instances rather than
microVMs: there is no GPU on the serverless compute type, so a locally hosted
generative model is not merely slower there, it is impossible.

The model stack is not in this container image. An AgentCore Runtime image is
capped at 2 GB and a CUDA torch build is 3.13 GB of wheels before weights, so the
stack is built onto the capacity provider's ``models`` volume by ``mode=prepare``
and invoked as a subprocess. See model_stack/prepare.py.

Packaged as a container image in Amazon ECR. It also owns creation of each track
directory on the shared volume, because a container agent and a zip agent run as
different Linux identities and whichever creates a directory first can lock the
other out.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import audio_dsp as audio
import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import BaseModel, Field, field_validator
from strands import Agent
from strands.models import BedrockModel
from strands.session import FileSessionManager

AGENT_NAME = "composition"
PROCESS_ID = uuid.uuid4().hex[:8]

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/mnt/tracks"))
MODEL_VOLUME = Path(os.environ.get("MODELS_DIR", "/mnt/models"))
# A "global." inference profile on purpose: a "us."-prefixed profile does not
# resolve outside US Regions, and this stack is deliberately Region-portable
# because GPU capacity is what dictates where it lands.
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
ARTIFACT_BUCKET = os.environ.get("ARTIFACT_BUCKET")

# Rendering is fast on an L4 (measured: 27 steps in ~2.4 s), so the ceiling here
# is generous only to absorb a cold model load.
RENDER_TIMEOUT_S = int(os.environ.get("RENDER_TIMEOUT_S", "900"))
PREPARE_TIMEOUT_S = int(os.environ.get("PREPARE_TIMEOUT_S", "1500"))

SYSTEM_PROMPT = """You are an AI music composition specialist.

You produce briefs that a generative audio model will render, so be concrete and
musical. Give a title, key, tempo, time signature, a section-by-section
arrangement, and the instrumentation.

`style_tags` is what reaches the audio model. It must be a comma-separated list
of concrete musical descriptors -- genre, instrumentation, mood, tempo -- and
nothing else. Good: "melodic techno, analog bass, warm pads, sidechained kick,
124 bpm". Bad: a sentence, or a section-by-section description.

When asked to remediate a copyright issue, produce a genuinely different piece:
change the key, change the tempo, and change the melodic and harmonic approach.
Write it as a fresh brief in the same format. Do not name, quote or describe the
work that was infringed, and do not narrate what you changed -- downstream agents
read your output as the composition itself.

Keep prose under 300 words."""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(AGENT_NAME)

app = BedrockAgentCoreApp()


class WorkflowError(Exception):
    """A recoverable problem in the pipeline, reported to the caller as data."""


class Section(BaseModel):
    name: str = Field(description="Intro, Verse, Chorus, Breakdown, Outro, ...")
    bars: int = Field(default=8)
    description: str = Field(default="")


class CompositionBrief(BaseModel):
    title: str = Field(description="A short evocative title.")
    key: str = Field(default="A minor")
    tempo_bpm: int = Field(default=124)
    time_signature: str = Field(default="4/4")
    chord_progression: str = Field(default="")
    instrumentation: list[str] = Field(default_factory=list)
    structure: list[Section] = Field(default_factory=list)
    style_tags: str = Field(description="Comma-separated concrete descriptors for the audio model.")
    lyrics: str = Field(default="[inst]", description="'[inst]' for an instrumental.")

    @field_validator("tempo_bpm")
    @classmethod
    def _sane_tempo(cls, v):
        return int(min(max(int(v), 50), 200))

    @field_validator("instrumentation", "structure", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                return [s.strip() for s in v.split(",") if s.strip()]
        return v if isinstance(v, list) else []

    @field_validator("style_tags", mode="before")
    @classmethod
    def _flatten_tags(cls, v):
        # Models sometimes return a list here despite the schema saying string.
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        return v

    def to_markdown(self, render: dict | None = None) -> str:
        lines = [
            f"# {self.title}",
            "",
            (f"**Key:** {self.key}  |  **Tempo:** {self.tempo_bpm} BPM  |  **Time signature:** {self.time_signature}"),
            "",
        ]
        if self.chord_progression:
            lines += [f"**Chord progression:** {self.chord_progression}", ""]
        if self.instrumentation:
            lines += ["**Instrumentation:** " + ", ".join(self.instrumentation), ""]
        if self.structure:
            lines += ["## Arrangement", ""]
            lines += [f"- **{s.name}** ({s.bars} bars) — {s.description}".rstrip(" —") for s in self.structure]
            lines.append("")
        lines += ["## Style tags given to the audio model", "", f"`{self.style_tags}`", ""]
        if render:
            a = render.get("audio", {})
            t = render.get("timings", {})
            lines += [
                "## Render",
                "",
                (
                    f"- {a.get('duration_s')} s, {a.get('sample_rate')} Hz, "
                    f"{a.get('channels')} ch, peak {a.get('peak_dbfs')} dBFS"
                ),
                (
                    f"- generated in {t.get('generate_s')} s on {render.get('device')} "
                    f"(peak VRAM {t.get('peak_vram_gib')} GiB)"
                ),
                "",
            ]
        return "\n".join(lines)


# --------------------------------------------------------------------- workspace


def track_path(track_id: str) -> Path:
    safe = "".join(c for c in track_id if c.isalnum() or c in "-_")[:64] or "track"
    return WORKSPACE / safe


def ensure_track_dir(track_id: str) -> Path:
    """Create this track's directory, writable by the other agents.

    Only ever called during an invocation: the volume is not mounted while the
    container is still initialising. Made group-writable with the setgid bit
    because a container agent (namespaced root, supplementary group
    agentcore-runtime-user) and a zip agent (a real host user in that same group)
    are different identities. chmod is best-effort; only the creator can change
    the mode.
    """
    path = track_path(track_id)
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o2775)
    except PermissionError:
        logger.debug("could not chmod %s - created by another identity", path)
    return path


def write_text(track_id: str, name: str, text: str) -> Path:
    p = track_path(track_id) / name
    p.write_text(text, encoding="utf-8")
    try:
        p.chmod(0o664)
    except PermissionError:
        pass
    logger.info("wrote %s (%d bytes)", p, len(text))
    return p


def read_text(track_id: str, name: str) -> str | None:
    p = track_path(track_id) / name
    return p.read_text(encoding="utf-8") if p.exists() else None


def list_artifacts(track_id: str) -> list[str]:
    p = track_path(track_id)
    return sorted(x.name for x in p.iterdir() if x.is_file()) if p.is_dir() else []


def publish(track_id: str, path: Path) -> dict:
    """Copy an artifact to S3 and hand back a link the caller can open.

    The volume lives inside a managed instance with no shell and is destroyed
    with the session, so S3 is the only route by which rendered audio reaches
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


# ------------------------------------------------------------------ model stack


# AgentCore injects the NVIDIA driver into /usr/lib64, which a Debian-based image
# does not search. Set in the Dockerfile too; repeated here so the subprocess is
# correct even if this agent is run from a differently built image.
DRIVER_LIB_DIR = "/usr/lib64"


def gpu_env() -> dict[str, str]:
    """Environment for the render subprocess, with the driver on the link path.

    Without /usr/lib64 on LD_LIBRARY_PATH, libcuda.so.1 exists but cannot be
    loaded, and torch falls back to the CPU silently rather than failing. Measured
    on a live g6.xlarge.
    """
    env = dict(os.environ)
    existing = env.get("LD_LIBRARY_PATH", "")
    parts = [DRIVER_LIB_DIR] + [p for p in existing.split(":") if p and p != DRIVER_LIB_DIR]
    env["LD_LIBRARY_PATH"] = ":".join(parts)
    return env


def model_stack_status() -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "model_stack"))
    import prepare as prep

    return prep.status(MODEL_VOLUME)


def prepare_model_stack() -> dict:
    """Build the venv and download weights onto the models volume.

    Deliberately runs in this process rather than through
    InvokeAgentRuntimeCommand: the mount is 2775 root:agentcore-runtime-user and
    only the agent process holds that supplementary group. A command shell gets
    EACCES here -- measured on a live instance.
    """
    if not MODEL_VOLUME.is_dir():
        raise WorkflowError(
            f"{MODEL_VOLUME} is not mounted. The composition runtime needs a "
            "capacityProviderVolume named 'models' in its filesystemConfigurations."
        )
    script = Path(__file__).resolve().parent / "model_stack" / "prepare.py"
    logger.info("preparing model stack at %s (several minutes on a cold volume)", MODEL_VOLUME)

    # Streamed rather than captured. subprocess.run(capture_output=True) buffers
    # everything until the process exits, so a five-minute build produced no
    # CloudWatch output at all and looked indistinguishable from a hang. Lines are
    # logged as they arrive and also kept for the response.
    deadline = time.monotonic() + PREPARE_TIMEOUT_S
    lines: list[str] = []
    proc = subprocess.Popen(
        [sys.executable, "-u", str(script), "prepare", str(MODEL_VOLUME)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=gpu_env(),
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                logger.info("prepare | %s", line[:500])
                lines.append(line)
            if time.monotonic() > deadline:
                proc.kill()
                raise WorkflowError(f"model stack preparation exceeded {PREPARE_TIMEOUT_S}s")
        returncode = proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()

    tail = "\n".join(lines[-60:])
    if returncode != 0:
        raise WorkflowError(f"model stack preparation failed (rc={returncode}):\n{tail}")
    logger.info("model stack prepared")
    return {**model_stack_status(), "log_tail": tail}


def render_audio(
    out_path: Path,
    brief: CompositionBrief,
    seed: int | None = None,
    duration_s: float = 30.0,
    steps: int = 27,
    reference_audio: Path | None = None,
    reference_strength: float = 0.5,
) -> dict:
    """Run the generator in its own interpreter on the models volume."""
    status = model_stack_status()
    if not status.get("ready"):
        raise WorkflowError(
            "The model stack on the shared volume is not ready "
            f"(status: {json.dumps(status)}). Invoke this runtime with "
            '{"mode": "prepare"} once per session before composing.'
        )

    cmd = [
        status["python"],
        status["runner"],
        "--checkpoint-dir",
        status["weights_dir"],
        "--out",
        str(out_path),
        "--prompt",
        brief.style_tags,
        "--lyrics",
        brief.lyrics or "[inst]",
        "--duration",
        str(duration_s),
        "--steps",
        str(steps),
    ]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if reference_audio is not None:
        cmd += ["--reference-audio", str(reference_audio), "--reference-strength", str(reference_strength)]

    logger.info("rendering: %s", " ".join(cmd[2:]))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=RENDER_TIMEOUT_S, env=gpu_env())
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    match = re.search(r"__RENDER_RESULT__ (\{.*\})", combined)
    if proc.returncode != 0 or not match:
        raise WorkflowError(f"render failed (rc={proc.returncode}). Tail of output:\n{combined[-3000:]}")
    result = json.loads(match.group(1))
    if result.get("audio", {}).get("silent"):
        raise WorkflowError("the generator produced a silent file")
    try:
        out_path.chmod(0o664)
    except PermissionError:
        pass
    return result


# ------------------------------------------------------------------------ agent


def build_agent(session_id: str, track_id: str) -> Agent:
    """Construct a fresh Agent for one invocation.

    A module-level Agent would be shared by every concurrent request, and Strands
    rejects re-entrant invocation ("Agent is already processing a request"), so a
    per-request Agent is a correctness requirement rather than a style choice.
    Conversation history is carried on the volume instead, which is what lets a
    session resumed days later remember earlier decisions.
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
            # Per-agent, not shared: FileSessionManager creates its storage
            # directory with mode 0700, so a directory shared between agents
            # running as different identities locks all but the first one out.
            storage_dir=str(track_path(track_id) / f".sessions-{AGENT_NAME}"),
        ),
    )


def compose_brief(agent: Agent, task: str) -> CompositionBrief:
    result = agent(task, structured_output_model=CompositionBrief)
    brief = result.structured_output
    if brief is None:
        raise WorkflowError("the model did not return a composition brief")
    return brief


@app.entrypoint
def invoke(payload, context):
    """AgentCore POSTs the invocation payload here.

    The second parameter must be named exactly ``context`` for the SDK to pass
    the request context, which is the only way to read the session id.
    """
    session_id = getattr(context, "session_id", None) or "local-session"
    track_id = payload.get("track_id", "demo-track")
    mode = payload.get("mode", "compose")
    prompt = payload.get("prompt") or "Compose an upbeat electronic track."
    if not isinstance(prompt, str):
        # The payload is arbitrary JSON, and a non-string here can carry toolUse
        # content blocks straight into the framework's event loop.
        return {"status": "error", "agent": AGENT_NAME, "error": "'prompt' must be a string", "host": host_info()}

    duration_s = float(payload.get("duration_s", 30.0))
    steps = int(payload.get("steps", 27))
    seed = payload.get("seed")

    logger.info("invoke: mode=%s track=%s session=%s model=%s", mode, track_id, session_id, MODEL_ID)

    try:
        if mode == "status":
            return {
                "status": "ok",
                "agent": AGENT_NAME,
                "mode": mode,
                "model_stack": model_stack_status(),
                "host": host_info(),
            }

        if mode == "prepare":
            # Preparation is per-session because the volume is per-session.
            stack = prepare_model_stack()
            return {
                "status": "ok",
                "agent": AGENT_NAME,
                "mode": mode,
                "session_id": session_id,
                "model_stack": stack,
                "host": host_info(),
            }

        # This agent owns creation of the track directory; the others require it.
        ensure_track_dir(track_id)

        if mode == "catalogue":
            # Render the fictional back-catalogue the compliance agent screens
            # against. Generating it with the same model keeps the sample
            # self-contained and free of any third-party recording.
            cat_dir = track_path(track_id) / "catalogue"
            cat_dir.mkdir(exist_ok=True)
            try:
                cat_dir.chmod(0o2775)
            except PermissionError:
                pass
            agent = build_agent(session_id, track_id)
            entries = []
            for i, style in enumerate(
                payload.get("styles")
                or [
                    "melodic techno, analog bass, warm pads, 124 bpm",
                    "lo-fi hip hop, dusty piano, vinyl crackle, 82 bpm",
                ]
            ):
                brief = compose_brief(
                    agent,
                    f"Write a brief for a catalogue reference track in this "
                    f"style: {style}. Keep style_tags close to that description.",
                )
                out = cat_dir / f"catalogue_{i:02d}.wav"
                render = render_audio(
                    out, brief, seed=1000 + i, duration_s=float(payload.get("duration_s", 20.0)), steps=steps
                )
                entries.append(
                    {"file": out.name, "title": brief.title, "style_tags": brief.style_tags, "render": render}
                )
            write_text(track_id, "catalogue.json", json.dumps(entries, indent=2))
            return {
                "status": "ok",
                "agent": AGENT_NAME,
                "mode": mode,
                "track_id": track_id,
                "session_id": session_id,
                "catalogue": entries,
                "host": host_info(),
            }

        agent = build_agent(session_id, track_id)

        if mode == "remediate":
            # The most recent attempt, not the original: a second remediation round
            # should diverge from what was last rejected, not from where it started.
            flagged = read_text(track_id, "composition_remediated.md") or read_text(track_id, "composition.md")
            if flagged is None:
                raise WorkflowError(
                    "No composition brief in the shared workspace - run the "
                    "composition step before requesting remediation."
                )
            issue = payload.get("issue") or prompt
            avoid = payload.get("avoid") or {}
            task = (
                f"A compliance screen flagged this issue:\n\n{issue}\n\n"
                f"Here is the brief it applies to:\n\n{flagged}\n\n"
            )
            if avoid.get("style_tags"):
                # This is the difference between a blind rewrite and an informed
                # one. Measured: without these descriptors the replacement moved
                # chroma distance only 0.029 -> 0.093, barely past the threshold.
                task += (
                    "The material it resembles was generated from these descriptors, "
                    f"so move decisively away from them:\n\n"
                    f"  flagged reference: {avoid.get('title') or avoid['reference']}\n"
                    f"  its style tags:    {avoid['style_tags']}\n\n"
                )
                others = [o for o in (avoid.get("other_references") or []) if o.get("style_tags")]
                if others:
                    task += (
                        "Also stay away from the rest of the catalogue:\n"
                        + "".join(f"  - {o['style_tags']} (distance {o['distance']})\n" for o in others)
                        + "\n"
                    )
            task += (
                "Write a complete standalone replacement brief. Change the key, change "
                "the tempo by at least 15 BPM, and choose a different genre lineage, "
                "different instrumentation and a different harmonic centre. Your "
                "style_tags must not reuse the flagged descriptors above -- pick "
                "different genre words, different instruments and a different mood. "
                "Do not name, quote or describe the flagged work, and do not narrate "
                "the changes: downstream agents read your output as the composition."
            )
            md_name, wav_name = "composition_remediated.md", "composition_remediated.wav"
        else:
            task = f"{prompt}\n\nTarget length: about {duration_s:.0f} seconds."
            md_name, wav_name = "composition.md", "composition.wav"

        brief = compose_brief(agent, task)
        wav = track_path(track_id) / wav_name

        reference = None
        if payload.get("imitate_catalogue"):
            # Used only to demonstrate detection: conditioning the generator on a
            # catalogue track produces genuinely derivative audio, which is a
            # far more honest test of the compliance agent than a text file that
            # merely claims to be a copy.
            reference = track_path(track_id) / "catalogue" / str(payload["imitate_catalogue"])
            if not reference.exists():
                raise WorkflowError(f"reference {reference.name} not found; run mode=catalogue first")

        render = render_audio(
            wav,
            brief,
            seed=seed,
            duration_s=duration_s,
            steps=steps,
            reference_audio=reference,
            reference_strength=float(payload.get("reference_strength", 0.75)),
        )
        measured = audio.measure(str(wav)).to_dict()
        markdown = brief.to_markdown(render)
        write_text(track_id, md_name, markdown)

        artifacts = [publish(track_id, wav), publish(track_id, track_path(track_id) / md_name)]

        return {
            "status": "ok",
            "agent": AGENT_NAME,
            "model": MODEL_ID,
            "mode": mode,
            "track_id": track_id,
            "session_id": session_id,
            "brief": brief.model_dump(),
            "result": markdown,
            "render": render,
            "measurements": measured,
            "artifacts": artifacts,
            "workspace_files": list_artifacts(track_id),
            "host": host_info(),
        }

    except WorkflowError as exc:
        # A pipeline-ordering problem is a real outcome, not a crash: report it
        # as data. Unexpected exceptions are deliberately left to propagate so
        # the service surfaces a 424 and the traceback reaches CloudWatch.
        logger.warning("workflow error: %s", exc)
        return {
            "status": "error",
            "agent": AGENT_NAME,
            "mode": mode,
            "track_id": track_id,
            "session_id": session_id,
            "error": str(exc),
            "workspace_files": list_artifacts(track_id),
            "host": host_info(),
        }
    except subprocess.TimeoutExpired as exc:
        logger.warning("subprocess timeout: %s", exc)
        return {
            "status": "error",
            "agent": AGENT_NAME,
            "mode": mode,
            "track_id": track_id,
            "session_id": session_id,
            "error": f"timed out after {exc.timeout}s",
            "host": host_info(),
        }


if __name__ == "__main__":
    # Bind explicitly. app.run() with no host guesses 0.0.0.0 only when it finds
    # /.dockerenv or DOCKER_CONTAINER, neither of which exists under Finch or
    # containerd - it would otherwise bind 127.0.0.1 and the runtime could never
    # reach port 8080. AgentCore sets PORT, which run() does not consult.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
