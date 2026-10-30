"""Render audio with ACE-Step. Runs in the model venv on the ``models`` volume.

Invoked as a subprocess by the composition agent, never imported by it. That
isolation is deliberate:

* The agent's container image is capped at 2 GB and cannot hold a CUDA torch
  build, so the whole ML stack lives on a mounted volume instead.
* A capacity provider volume is only mounted at invocation time, not during
  container initialisation, so a module-level ``import torch`` in the agent could
  not work even if it fitted.
* ACE-Step pins ``transformers==4.50.0`` and pulls in gradio, spacy and
  tensorboard. Keeping it in its own interpreter means none of that has to
  co-resolve with the agent's Strands dependencies.

Communicates back over stdout: ACE-Step logs freely, so the machine-readable
result is emitted on one line behind a sentinel.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

RESULT_SENTINEL = "__RENDER_RESULT__"


def patch_torchaudio_io() -> str:
    """Route torchaudio's file I/O through soundfile, bypassing TorchCodec.

    Recent torchaudio delegates both ``save`` and ``load`` to TorchCodec, which is
    not in its dependency tree and not in ACE-Step's either. Measured failures on
    a live g6.xlarge:

      ImportError: TorchCodec is required for save_with_torchcodec   (writing output)
      ImportError: TorchCodec is required for load_with_torchcodec   (reading a
                                                                     reference for
                                                                     audio2audio)

    soundfile is already installed, so both are redirected to it. Patching rather
    than adding torchcodec keeps the dependency set to what ACE-Step already
    resolves, and lets us pin the output container to 24-bit PCM.
    """
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio

    def _save(uri, src, sample_rate, **kwargs):
        arr = src.detach().cpu().to(torch.float32).numpy() if hasattr(src, "detach") else np.asarray(src)
        if arr.ndim == 1:
            arr = arr[None, :]
        # torchaudio is (channels, samples); soundfile wants (samples, channels).
        sf.write(str(uri), arr.T, int(sample_rate), subtype="PCM_24")

    def _load(uri, frame_offset=0, num_frames=-1, normalize=True, channels_first=True, **kwargs):
        data, rate = sf.read(
            str(uri),
            always_2d=True,
            dtype="float32",
            start=int(frame_offset),
            frames=int(num_frames) if num_frames and num_frames > 0 else -1,
        )
        tensor = torch.from_numpy(data)  # (samples, channels)
        if channels_first:
            tensor = tensor.transpose(0, 1)  # -> (channels, samples)
        return tensor.contiguous(), int(rate)

    torchaudio.save = _save
    torchaudio.load = _load
    return "torchaudio.save and torchaudio.load redirected to soundfile (TorchCodec absent)"


def describe(path: str) -> dict:
    """Confirm we produced real audio rather than a silent file of the right size."""
    import numpy as np
    import soundfile as sf

    data, rate = sf.read(path, always_2d=True, dtype="float64")
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    rms = float(np.sqrt(np.mean(data**2))) if data.size else 0.0
    return {
        "path": path,
        "bytes": os.path.getsize(path),
        "sample_rate": int(rate),
        "channels": int(data.shape[1]),
        "duration_s": round(data.shape[0] / rate, 3),
        "peak_dbfs": round(20 * math.log10(peak), 2) if peak > 0 else None,
        "rms_dbfs": round(20 * math.log10(rms), 2) if rms > 0 else None,
        "silent": peak < 1e-5,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt", required=True, help="Style tags, e.g. 'upbeat electronic, heavy bass, 128 bpm'")
    ap.add_argument("--lyrics", default="[inst]", help="'[inst]' renders an instrumental.")
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--steps", type=int, default=27)
    ap.add_argument("--guidance", type=float, default=15.0)
    ap.add_argument("--seed", type=int, default=None)
    # audio2audio is how the sample produces a deliberate near-copy of its own
    # back-catalogue, so the compliance agent has something real to detect.
    ap.add_argument("--reference-audio", default=None)
    ap.add_argument("--reference-strength", type=float, default=0.5)
    args = ap.parse_args()

    timings: dict = {}
    notes = [patch_torchaudio_io()]

    t0 = time.time()
    import torch
    from acestep.pipeline_ace_step import ACEStepPipeline

    timings["import_s"] = round(time.time() - t0, 2)

    if not torch.cuda.is_available():
        # Worth failing loudly: on a capacity provider instance the NVIDIA driver
        # is injected by AgentCore, so an absent GPU means the runtime is not on
        # the fleet we think it is.
        print(json.dumps({"error": "CUDA is not available in the model venv"}), file=sys.stderr)
        return 2
    device = torch.cuda.get_device_name(0)

    t0 = time.time()
    pipe = ACEStepPipeline(checkpoint_dir=args.checkpoint_dir, dtype="bfloat16", torch_compile=False)
    timings["pipeline_ctor_s"] = round(time.time() - t0, 2)

    if args.seed is not None:
        torch.manual_seed(args.seed)

    call: dict = {
        "format": "wav",
        "prompt": args.prompt,
        "lyrics": args.lyrics or "[inst]",
        "audio_duration": float(args.duration),
        "infer_step": int(args.steps),
        "guidance_scale": float(args.guidance),
        "scheduler_type": "euler",
        "cfg_type": "apg",
        "omega_scale": 10.0,
        "save_path": args.out,
    }
    if args.seed is not None:
        call["manual_seeds"] = [int(args.seed)]
    if args.reference_audio:
        call.update(
            audio2audio_enable=True,
            ref_audio_input=args.reference_audio,
            ref_audio_strength=float(args.reference_strength),
        )

    t0 = time.time()
    pipe(**call)
    timings["generate_s"] = round(time.time() - t0, 2)
    timings["peak_vram_gib"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)

    if not os.path.exists(args.out):
        print(json.dumps({"error": f"pipeline did not write {args.out}"}), file=sys.stderr)
        return 3

    result = {
        "ok": True,
        "device": device,
        "torch": torch.__version__,
        "timings": timings,
        "audio": describe(args.out),
        "parameters": {k: v for k, v in call.items() if k != "save_path"},
        "notes": notes,
    }
    print(f"{RESULT_SENTINEL} {json.dumps(result)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
