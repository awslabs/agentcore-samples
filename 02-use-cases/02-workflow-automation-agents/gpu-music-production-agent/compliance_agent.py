"""Compliance agent.

Screens the finished master before release. Two independent checks, both on the
actual audio:

1. **Delivery QC.** Re-measures loudness, true peak, clipping, DC offset and mono
   compatibility, and compares them against the targets the mastering agent said
   it was hitting. The mastering agent grading its own homework is not a control;
   this is.

2. **Similarity screening.** Compares the master's harmonic content against
   the reference catalogue using chroma features and subsequence DTW over
   all twelve transpositions, so a copy that was shifted to another key still
   matches. An acoustic fingerprint would only catch a byte-level duplicate.

If the screen flags the track, it calls back into the composition agent's
runtime -- same session, same instance -- for an original replacement, then
re-screens.

**This is a screen, not a copyright clearance.** It compares against one local
catalogue with one feature. A flag means escalate to a human or a licensed
identification service; a pass means nothing was found in that catalogue.

The verdict is computed from the measurements, not asked of the model. The model
writes the explanation and the remediation brief. That way the prose and the
verdict cannot disagree, which they can when a model is asked to do both.

Packaged as a zip artifact on Amazon S3 rather than a container image, which is
the point of shipping it alongside the other two: artifact type is a per-team
choice and mixed artifacts coexist on one capacity provider. It stays a zip
because its dependencies are numpy, scipy and soundfile -- about 54 MB against
the 250 MB compressed limit. A torch-based embedding model would not fit and
would force it to become a container.

Note on trust: agents collocated on one instance are not isolated from each other
and can read each other's credentials, so only mutually trusted agents belong in
a session. All three here are first-party. A third-party vendor's agent belongs
on its own capacity provider.
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
from botocore.config import Config
from pydantic import BaseModel, Field, field_validator
from strands import Agent
from strands.models import BedrockModel
from strands.session import FileSessionManager

AGENT_NAME = "compliance"
PROCESS_ID = uuid.uuid4().hex[:8]

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/mnt/tracks"))
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
ARTIFACT_BUCKET = os.environ.get("ARTIFACT_BUCKET")

# Set by deploy.py once the composition runtime exists. Its ARN cannot be
# hand-written: CreateAgentRuntime appends a random 10-character suffix.
COMPOSITION_RUNTIME_ARN = os.environ.get("COMPOSITION_RUNTIME_ARN")
COMPOSITION_QUALIFIER = os.environ.get("COMPOSITION_QUALIFIER", "DEFAULT")

# Absolute distance thresholds. These are empirical and, on their own, NOT
# sufficient -- which is the honest lesson from calibrating them:
#
#   synthetic melodies : identical 0.000 | transposed 0.002 | unrelated 0.273
#   real generated audio, derivative     : 0.029, 0.050
#   real generated audio, unrelated      : 0.062, 0.063, 0.093, 0.122
#
# Those two real-audio ranges OVERLAP, so no single cut separates a copy from an
# original: 0.08 failed an unrelated track, and 0.045 cleared a track that had been
# conditioned directly on a catalogue reference. The relative STANDOUT_RATIO test
# below is what actually discriminates; these absolutes are the coarse first pass.
# Recalibrate all three against your own catalogue before trusting them.
FAIL_DISTANCE = float(os.environ.get("SIMILARITY_FAIL_DISTANCE", "0.045"))
REVIEW_DISTANCE = float(os.environ.get("SIMILARITY_REVIEW_DISTANCE", "0.10"))

# The relative test. If the closest match is less than this fraction of the
# next-closest distance, one catalogue track stands out and that is suspicious
# regardless of the absolute number. Measured derivative ratios: 0.239 and 0.384.
# Measured original: 0.98. Needs at least two references to mean anything.
STANDOUT_RATIO = float(os.environ.get("SIMILARITY_STANDOUT_RATIO", "0.6"))

SYSTEM_PROMPT = """You are a music copyright and delivery compliance analyst.

You are given measurements of a finished master and the output of a similarity
screen against the reference catalogue. Explain what the numbers mean for
release readiness, in plain language, for a producer who is not an engineer.

Rules:
- The screen has already decided every comparison. State its conclusions and
  explain what they mean musically. Do not re-derive a verdict, and do not compare
  a measurement against a threshold -- the report prints the figures itself.
- Do not speculate about infringement the screen did not find, and do not dismiss
  a flag it did raise.
- Common chord progressions and standard song forms are not infringement.
- If a match was flagged or needs review, describe precisely what a replacement
  must change: the harmonic movement, the melodic contour, the key.
- Be explicit that this is a screen against one catalogue, not a legal clearance.

Under 300 words."""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(AGENT_NAME)

app = BedrockAgentCoreApp()


class WorkflowError(Exception):
    """A recoverable problem in the pipeline, reported to the caller as data."""


class Review(BaseModel):
    """The model's explanation. It does not decide the verdict."""

    summary: str = Field(description="Two sentences on release readiness.")
    explanation: str = Field(default="", description="What the measurements mean.")
    remediation_instruction: str = Field(
        default="", description="If a flag was raised, precisely what a replacement must change."
    )

    @field_validator("summary", "explanation", "remediation_instruction", mode="before")
    @classmethod
    def _stringify(cls, v):
        if v is None:
            return ""
        if isinstance(v, (list, dict)):
            return json.dumps(v)
        return str(v)


# --------------------------------------------------------------------- workspace


def track_path(track_id: str) -> Path:
    safe = "".join(c for c in track_id if c.isalnum() or c in "-_")[:64] or "track"
    return WORKSPACE / safe


def require_track_dir(track_id: str) -> Path:
    """Fail loudly if the composition agent has not created the track yet.

    Deliberately does not create it: this agent ships as a zip and runs as a real
    host user, while the container agents run as a namespaced root. Whichever
    identity creates a directory first can lock the other out, so creation
    belongs to the composition agent alone.
    """
    p = track_path(track_id)
    if not p.is_dir():
        raise WorkflowError(
            f"No workspace for track '{track_id}'. Invoke the composition agent first, with the same runtimeSessionId."
        )
    return p


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


def list_artifacts(track_id: str) -> list[str]:
    p = track_path(track_id)
    return sorted(x.name for x in p.iterdir() if x.is_file()) if p.is_dir() else []


def subject_audio(track_id: str) -> tuple[str, Path, bool]:
    """What is actually under review, and whether the master is stale.

    The master is the right subject only while it is newer than the newest render.
    After a remediation the composition agent has written a fresh
    composition_remediated.wav and nothing has re-mastered it, so screening
    master.wav again would re-judge the very material that was just replaced --
    measured: a remediated track stayed NOT CLEARED because the stale master was
    screened a second time.

    Returns (name, path, master_is_stale).
    """
    directory = track_path(track_id)
    renders = [
        p
        for p in (directory / "composition_remediated.wav", directory / "composition.wav")
        if p.exists() and p.stat().st_size > 0
    ]
    master = directory / "master.wav"
    has_master = master.exists() and master.stat().st_size > 0

    if has_master and renders:
        newest_render = max(renders, key=lambda p: p.stat().st_mtime)
        if newest_render.stat().st_mtime > master.stat().st_mtime:
            # The master predates the current render: screen the render and say so.
            return newest_render.name, newest_render, True
        return master.name, master, False
    if has_master:
        return master.name, master, False
    if renders:
        newest = max(renders, key=lambda p: p.stat().st_mtime)
        return newest.name, newest, False
    raise WorkflowError(
        "No audio to review in the shared workspace. Invoke the composition and "
        "mastering agents first, with the same runtimeSessionId."
    )


def publish(track_id: str, path: Path) -> dict:
    """Copy an artifact to S3 and hand back a link the caller can open.

    The volume lives inside a managed instance with no shell and is destroyed
    with the session, so S3 is the only route by which a report reaches whoever
    invoked us.
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


# -------------------------------------------------------------------- the checks


def delivery_qc(path: Path, track_id: str, is_master: bool = True) -> dict:
    """Re-measure the subject and check it against what mastering claimed.

    ``is_master`` matters. Delivery targets only apply to a master; asserting them
    against a raw render makes the verdict guaranteed-fail after any remediation,
    because an unmastered file is of course not at -14 LUFS. Measured case: a
    freshly remediated render was failed for being -16.88 LUFS against a target it
    had never been through mastering to meet. Those two checks become
    informational when the subject is not the master.
    """
    m = audio.measure(str(path)).to_dict()
    claimed: dict = {}
    source_lra: float | None = None
    raw = read_text(track_id, "mastering.json")
    if raw:
        try:
            result = json.loads(raw).get("result") or {}
            claimed = result.get("targets") or {}
            # What the mix measured BEFORE mastering, so the dynamics check can
            # ask whether mastering crushed the material rather than whether the
            # material had dynamics in the first place.
            source_lra = (result.get("before") or {}).get("loudness_range_lu")
        except json.JSONDecodeError:
            claimed = {}
    target_lufs = claimed.get("lufs")
    target_peak = claimed.get("true_peak_dbtp")

    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    if target_lufs is not None and m["integrated_lufs"] is not None:
        err = abs(m["integrated_lufs"] - target_lufs)
        detail = f"{m['integrated_lufs']} LUFS against a {target_lufs} LUFS target (error {err:.2f} LU, tolerance 1.0)"
        if is_master:
            check("integrated_loudness", err <= 1.0, detail)
        else:
            checks.append(
                {
                    "check": "integrated_loudness",
                    "pass": True,
                    "detail": f"not applicable to an unmastered render: {detail}",
                }
            )
    if target_peak is not None and m["true_peak_dbtp"] is not None:
        detail = f"{m['true_peak_dbtp']} dBTP against a {target_peak} dBTP ceiling"
        if is_master:
            check("true_peak_ceiling", m["true_peak_dbtp"] <= target_peak + 0.1, detail)
        else:
            checks.append(
                {
                    "check": "true_peak_ceiling",
                    "pass": True,
                    "detail": f"not applicable to an unmastered render: {detail}",
                }
            )
    check("no_clipping", m["clipped_runs"] == 0, f"{m['clipped_runs']} run(s) of consecutive full-scale samples")
    check("dc_offset", m["dc_offset"] < 0.005, f"DC offset {m['dc_offset']}")
    check("not_silent", not m["silent"], "audio is present")
    if m["stereo_correlation"] is not None:
        check("mono_compatible", m["stereo_correlation"] > -0.2, f"inter-channel correlation {m['stereo_correlation']}")
    # Loudness range needs enough 3-second blocks to mean anything, and this
    # agent's job is to catch a master that destroyed the mix, not to fail a mix
    # that was uniform to begin with. Measured case that got this wrong: a 24 s
    # generated loop came in at 0.47 LU and went out at 0.6 LU, and an absolute
    # 1.0 LU floor blamed mastering for the source's own uniformity.
    if m["loudness_range_lu"] is None or (m["duration_s"] or 0) < 30:
        checks.append(
            {
                "check": "dynamics_retained",
                "pass": True,
                "detail": f"not assessed: {m['duration_s']}s is too short for a meaningful loudness range",
            }
        )
    elif source_lra is not None:
        floor = max(0.0, source_lra * 0.5 - 0.1)
        check(
            "dynamics_retained",
            m["loudness_range_lu"] >= floor,
            f"loudness range {m['loudness_range_lu']} LU against {source_lra} LU in the mix (at least half expected)",
        )
    else:
        check(
            "dynamics_retained",
            m["loudness_range_lu"] >= 1.0,
            f"loudness range {m['loudness_range_lu']} LU (no pre-master measurement available to compare against)",
        )

    return {
        "measurements": m,
        "claimed_targets": claimed,
        "source_lra": source_lra,
        "checks": checks,
        "passed": all(c["pass"] for c in checks),
    }


def catalogue_metadata(track_id: str) -> dict[str, dict]:
    """What the catalogue tracks were made from, keyed by filename.

    Read so that a flag can tell the composition agent *what to diverge from*
    rather than just "be different". catalogue.json is written by the composition
    agent's mode=catalogue and holds the style_tags and title of each reference.
    """
    raw = read_text(track_id, "catalogue.json")
    if not raw:
        return {}
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict] = {}
    for e in entries if isinstance(entries, list) else []:
        name = e.get("file")
        if name:
            out[name] = {"title": e.get("title"), "style_tags": e.get("style_tags")}
    return out


def similarity_screen(subject: Path, track_id: str) -> dict:
    """Compare the subject against the local back-catalogue."""
    cat_dir = track_path(track_id) / "catalogue"
    references = sorted(cat_dir.glob("*.wav")) if cat_dir.is_dir() else []
    if not references:
        return {
            "references": 0,
            "hits": [],
            "flagged": False,
            "note": "no back-catalogue on the volume; similarity was not screened",
        }

    metadata = catalogue_metadata(track_id)
    subject_chroma = audio.chroma(str(subject))
    hits = []
    for ref in references:
        distance, shift = audio.chroma_dtw_distance(subject_chroma, audio.chroma(str(ref)))
        hit = audio.SimilarityHit(reference=ref.name, distance=distance, semitone_shift=shift).to_dict()
        hit["verdict"] = (
            "near_duplicate" if distance < FAIL_DISTANCE else "review" if distance < REVIEW_DISTANCE else "clear"
        )
        # Carried through so remediation can be told what to move away from.
        hit.update(metadata.get(ref.name, {}))
        hits.append(hit)
    hits.sort(key=lambda h: h["distance"])
    closest = hits[0]

    # A second, relative signal, because absolute distance alone does not work.
    # Measured across runs: a genuine derivative scored 0.029 and 0.050 while
    # unrelated same-genre material scored 0.062 to 0.122 -- overlapping ranges, so
    # any single cut either misses copies or fails originals.
    #
    # What does separate them is how far the closest match stands out from the rest
    # of the catalogue. A derivative is much closer to one track than to the others
    # (ratios 0.239 and 0.384); an original is roughly equidistant (0.98).
    standout_ratio = None
    if len(hits) >= 2 and hits[1]["distance"] > 0:
        standout_ratio = round(closest["distance"] / hits[1]["distance"], 4)
    standout = standout_ratio is not None and standout_ratio < STANDOUT_RATIO

    below_fail = closest["distance"] < FAIL_DISTANCE
    return {
        "references": len(references),
        "hits": hits,
        "closest": closest,
        # Either signal is enough to flag: an outright close match, or one track
        # that stands out sharply from the rest of the catalogue.
        "flagged": bool(below_fail or standout),
        "flag_reason": (
            "distance below the fail threshold"
            if below_fail
            else "closest match stands out from the catalogue"
            if standout
            else None
        ),
        "needs_review": bool(not below_fail and not standout and closest["distance"] < REVIEW_DISTANCE),
        "standout_ratio": standout_ratio,
        "thresholds": {
            "fail_below": FAIL_DISTANCE,
            "review_below": REVIEW_DISTANCE,
            "standout_ratio_below": STANDOUT_RATIO,
        },
    }


def remediation_available() -> bool:
    return bool(COMPOSITION_RUNTIME_ARN)


def call_composition_runtime(session_id: str, track_id: str, issue: str, avoid: dict | None = None) -> tuple[bool, str]:
    """Invoke the composition agent's runtime for remediation.

    Uses the caller's own session id, so AgentCore routes the request to the
    composition agent already running on this instance instead of provisioning
    another one.

    Returns (remediated, message). The boolean matters: "the screen asked for a
    replacement" and "a replacement now exists" are different facts, and only the
    second licenses the entrypoint to go looking for the new file.
    """
    if not COMPOSITION_RUNTIME_ARN:
        return False, "Remediation is unavailable: COMPOSITION_RUNTIME_ARN is not set."
    client = boto3.client(
        "bedrock-agentcore",
        region_name=REGION,
        # A nested invocation runs inside this request's 15-minute budget, so the
        # read timeout must comfortably exceed the inner render. Retries stay on
        # to absorb RetryableConflictException (409).
        config=Config(read_timeout=600, retries={"max_attempts": 3, "mode": "standard"}),
    )
    response = client.invoke_agent_runtime(
        agentRuntimeArn=COMPOSITION_RUNTIME_ARN,
        qualifier=COMPOSITION_QUALIFIER,
        runtimeSessionId=session_id,
        payload=json.dumps(
            {
                "mode": "remediate",
                "track_id": track_id,
                "issue": issue,
                # The evidence, not just the complaint. Without this the
                # composition agent is rewriting blind: measured, a blind
                # rewrite moved chroma distance only 0.029 -> 0.093.
                "avoid": avoid or {},
            }
        ).encode(),
    )
    # The response body member is named "response", not "body".
    body = json.loads(response["response"].read())
    if body.get("status") != "ok":
        return False, f"Remediation failed: {body.get('error', 'unknown error')}"
    logger.info("remediation produced %s", [a.get("name") for a in body.get("artifacts", [])])
    return True, body.get("result", "")


def build_agent(session_id: str, track_id: str) -> Agent:
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
            # mode 0700, which locks out agents running as another identity.
            storage_dir=str(track_path(track_id) / f".sessions-{AGENT_NAME}"),
        ),
    )


def screen(track_id: str) -> dict:
    """Run both checks and compute the verdict from them."""
    name, subject, master_stale = subject_audio(track_id)
    qc = delivery_qc(subject, track_id, is_master=(name == "master.wav"))
    sim = similarity_screen(subject, track_id)
    # Three outcomes, not two. Auto-clearing anything the screen wanted a human to
    # look at is the one thing a compliance tool must not do -- measured: a track
    # conditioned directly on a catalogue reference scored 0.0498, landed in the
    # review band, and was reported CLEARED.
    if not qc["passed"] or sim["flagged"]:
        outcome = "not_cleared"
    elif sim.get("needs_review"):
        outcome = "review_required"
    else:
        outcome = "cleared"

    return {
        "subject": name,
        "master_is_stale": master_stale,
        "delivery_qc": qc,
        "similarity": sim,
        # The verdict is arithmetic on the measurements, never the model's call.
        # A stale master is not a release blocker; it is a "re-master this" signal,
        # so it does not fail the verdict on its own.
        "outcome": outcome,
        "passed": outcome == "cleared",
    }


def findings_brief(result: dict) -> str:
    """Render what the screen decided, in words, with no figures to compare.

    Every comparison in this pipeline is already resolved in code: each hit carries
    a verdict, the screen carries flagged / needs_review / flag_reason, and the
    outcome is computed arithmetic. Handing the model raw distances next to raw
    thresholds invites it to re-derive what has already been derived, and a smaller
    model did exactly that -- it wrote "0.0892 remains below the automatic fail
    threshold of 0.045" into a delivery report, conflating the fail and review
    thresholds it had been given by name. The conclusion happened to be right and
    the premise was false; only the computed verdict kept the report honest.

    Choosing a stronger model makes that rarer. Not asking makes it impossible, so
    the figures are withheld here -- render_review prints them from the same dict,
    straight out of the measurements, where no model can restate them wrongly.
    """
    qc, sim = result["delivery_qc"], result["similarity"]
    lines = [
        f"Subject file: {result['subject']}",
        (
            f"DECIDED OUTCOME: {result.get('outcome')} -- final, computed from the "
            "measurements. Explain it; do not re-derive it."
        ),
        "",
        f"Delivery QC: {'passed' if qc['passed'] else 'FAILED'}.",
    ]
    lines += [f"  - {c['check']}: {'pass' if c['pass'] else 'FAIL'} -- {c['detail']}" for c in qc.get("checks", [])]

    lines += ["", "Similarity screen:"]
    if not sim.get("references"):
        lines.append(f"  {sim.get('note', 'not screened')}")
        return "\n".join(lines)

    lines.append(f"  {sim['references']} catalogue reference(s), all 12 transpositions.")
    if sim.get("flagged"):
        lines.append(f"  FLAGGED, and the reason is: {sim['flag_reason']}.")
    elif sim.get("needs_review"):
        # needs_review is decided on the closest hit, but more than one can land in
        # the band -- count them rather than saying "one match" and being wrong.
        n = sum(1 for h in sim.get("hits", []) if h.get("verdict") == "review")
        lines.append(
            f"  Not flagged for blocking, but {n} match(es) fall in the band "
            "that requires manual review before release."
        )
    else:
        lines.append("  Nothing flagged, and nothing in the manual-review band.")

    states = {"near_duplicate": "near-duplicate", "review": "needs manual review", "clear": "cleared"}
    for h in sim.get("hits", []):
        # The transposition is musical information, not a threshold comparison, so
        # it stays. Distances and ratios do not.
        # catalogue.json may be absent, in which case there is no title to show and
        # repeating the filename in quotes reads like a bug.
        named = f' ("{h["title"]}")' if h.get("title") else ""
        line = (
            f"  - {h['reference']}{named}: "
            f"{states.get(h.get('verdict'), h.get('verdict'))}, "
            f"best alignment at {int(h.get('semitone_shift', 0)):+d} semitones"
        )
        if h.get("style_tags"):
            line += f", generated from: {h['style_tags']}"
        lines.append(line)
    return "\n".join(lines)


def render_review(result: dict, review: Review, remediated: bool) -> str:
    qc, sim = result["delivery_qc"], result["similarity"]
    label = {"cleared": "CLEARED", "review_required": "REVIEW REQUIRED", "not_cleared": "NOT CLEARED"}[
        result.get("outcome", "not_cleared")
    ]
    lines = [
        f"# Compliance Review: `{result['subject']}`",
        "",
        f"**Verdict: {label}**",
        "",
        review.summary,
        "",
        "## Delivery QC",
        "",
        "| check | result | detail |",
        "|---|---|---|",
    ]
    for c in qc["checks"]:
        lines.append(f"| {c['check']} | {'pass' if c['pass'] else 'FAIL'} | {c['detail']} |")
    lines += ["", "## Similarity screen", ""]
    if not sim.get("references"):
        lines.append(f"_{sim.get('note')}_")
    else:
        preamble = f"Screened against {sim['references']} catalogue reference(s), all 12 transpositions."
        if sim.get("standout_ratio") is not None:
            preamble += (
                f" Closest match is {sim['standout_ratio']}x the next-closest "
                f"distance (flagged below {sim['thresholds']['standout_ratio_below']})."
            )
        if sim.get("flag_reason"):
            preamble += f" **Flagged: {sim['flag_reason']}.**"
        lines += [preamble, "", "| reference | distance | similarity | shift | verdict |", "|---|---|---|---|---|"]
        for h in sim["hits"]:
            lines.append(
                f"| {h['reference']} | {h['distance']} | {h['similarity']} | "
                f"{h['semitone_shift']:+d} st | {h['verdict']} |"
            )
    if review.explanation:
        lines += ["", "## Analysis", "", review.explanation]
    if not result["passed"] and review.remediation_instruction:
        lines += ["", "## Required changes", "", review.remediation_instruction]
    if remediated:
        lines += [
            "",
            (
                "_A replacement was requested from the composition agent and "
                "re-screened; the verdict above is for the replacement._"
            ),
        ]
    if result.get("master_is_stale"):
        lines += [
            "",
            (
                "_Note: `master.wav` predates the render screened above, so it "
                "is stale. Re-run the mastering agent before release._"
            ),
        ]
    lines += [
        "",
        "---",
        "",
        (
            "_This is a screen against one local catalogue using one harmonic "
            "feature. It is not a copyright clearance. A flag means escalate to "
            "a human or a licensed identification service._"
        ),
    ]
    return "\n".join(lines)


@app.entrypoint
def invoke(payload, context):
    session_id = getattr(context, "session_id", None) or "local-session"
    track_id = payload.get("track_id", "demo-track")
    prompt = payload.get("prompt") or "Review this track for release."
    if not isinstance(prompt, str):
        return {"status": "error", "agent": AGENT_NAME, "error": "'prompt' must be a string", "host": host_info()}

    logger.info(
        "invoke: track=%s session=%s model=%s remediation=%s",
        track_id,
        session_id,
        MODEL_ID,
        "on" if remediation_available() else "off",
    )

    try:
        require_track_dir(track_id)
        result = screen(track_id)
        logger.info("screen: passed=%s closest=%s", result["passed"], result["similarity"].get("closest"))

        remediated = False
        if (
            not result["passed"]
            and result["similarity"]["flagged"]
            and remediation_available()
            and payload.get("auto_remediate", True)
        ):
            closest = result["similarity"]["closest"]
            issue = (
                f"The rendered track is harmonically near-identical to catalogue "
                f"reference {closest['reference']} (chroma-DTW distance "
                f"{closest['distance']}, transposed {closest['semitone_shift']:+d} "
                f"semitones, similarity {closest['similarity']}). The screen fails "
                f"anything below {FAIL_DISTANCE}; a replacement needs to score above "
                f"{REVIEW_DISTANCE} to clear without review."
            )
            # Hand over what the flagged reference actually is, so the rewrite can
            # move away from something specific instead of guessing.
            avoid = {
                "reference": closest["reference"],
                "title": closest.get("title"),
                "style_tags": closest.get("style_tags"),
                "distance": closest["distance"],
                "semitone_shift": closest["semitone_shift"],
                "fail_below": FAIL_DISTANCE,
                "clear_above": REVIEW_DISTANCE,
                "other_references": [
                    {"reference": h["reference"], "style_tags": h.get("style_tags"), "distance": h["distance"]}
                    for h in result["similarity"]["hits"][1:]
                ],
            }
            remediated, _ = call_composition_runtime(session_id, track_id, issue, avoid)
            if remediated:
                replacement = track_path(track_id) / "composition_remediated.wav"
                if not replacement.exists():
                    raise WorkflowError(
                        "The composition agent reported a successful remediation but "
                        "composition_remediated.wav is not on the shared volume - the "
                        "two agents are not seeing the same filesystem. Check that "
                        "both runtimes mount the same capacity provider volume and "
                        "were invoked with the same runtimeSessionId."
                    )
                # Re-screen. The subject is now the replacement, and because the
                # verdict is computed from measurements it cannot inherit the
                # earlier failure.
                result = screen(track_id)
                logger.info("re-screen after remediation: passed=%s", result["passed"])

        agent = build_agent(session_id, track_id)
        analysis = agent(
            f"Explain these compliance results for the producer.\n\n{findings_brief(result)}\n\nRequest: {prompt}",
            structured_output_model=Review,
        )
        review = analysis.structured_output or Review(
            summary="The model did not return an explanation; the computed verdict stands."
        )

        markdown = render_review(result, review, remediated)
        write_text(track_id, "compliance.md", markdown)
        report = {
            "verdict": {
                "passed": result["passed"],
                "outcome": result.get("outcome"),
                "remediation_requested": remediated,
            },
            "subject": result["subject"],
            "master_is_stale": result.get("master_is_stale", False),
            "delivery_qc": result["delivery_qc"],
            "similarity": result["similarity"],
            "review": review.model_dump(),
            "reviewed_files": list_artifacts(track_id),
        }
        write_text(track_id, "compliance.json", json.dumps(report, indent=2))

        artifacts = [
            publish(track_id, track_path(track_id) / "compliance.md"),
            publish(track_id, track_path(track_id) / "compliance.json"),
        ]

        return {
            "status": "ok",
            "agent": AGENT_NAME,
            "model": MODEL_ID,
            "track_id": track_id,
            "session_id": session_id,
            "read_from": result["subject"],
            "master_is_stale": result.get("master_is_stale", False),
            "validation_passed": result["passed"],
            "outcome": result.get("outcome"),
            "verdict": report["verdict"],
            "delivery_qc": result["delivery_qc"],
            "similarity": result["similarity"],
            "result": markdown,
            "artifacts": artifacts,
            "workspace_files": list_artifacts(track_id),
            "host": host_info(),
        }

    except WorkflowError as exc:
        logger.warning("workflow error: %s", exc)
        return {
            "status": "error",
            "agent": AGENT_NAME,
            "track_id": track_id,
            "session_id": session_id,
            "error": str(exc),
            "validation_passed": False,
            "workspace_files": list_artifacts(track_id),
            "host": host_info(),
        }


if __name__ == "__main__":
    # Explicit bind and explicit port: see the note in composition_agent.py.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
