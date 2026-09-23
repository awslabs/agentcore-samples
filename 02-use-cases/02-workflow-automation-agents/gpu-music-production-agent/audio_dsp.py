"""Audio measurement and processing, in numpy and scipy only.

This module is what turns the sample from three agents writing prose into three
agents doing verifiable work: the mastering agent applies real filters and the
compliance agent measures the result against the spec the mastering agent
claimed. Every number here is reproducible from the WAV file.

Deliberately has no third-party DSP dependency:

* ``pedalboard`` is GPL (it links JUCE), which rules it out for a published
  sample that customers adapt.
* ``pyloudnorm`` publishes no license metadata, and true-peak measurement is not
  in it anyway, so the loudness algorithm is implemented here instead.

That leaves numpy, scipy and soundfile, all BSD-3. It also means the loudness
maths is on the page rather than behind an import, which is the point of a
sample.

Loudness follows ITU-R BS.1770-4 and loudness range follows EBU Tech 3342. The
standard specifies its K-weighting coefficients at 48 kHz, so audio is resampled
to 48 kHz for measurement rather than the coefficients being re-derived per rate.

A copy of this file is placed in each agent artifact at build time. The agents
ship independently and must not import from one another, but there is no reason
for the DSP to be transcribed three times.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
import soundfile as sf
from scipy import signal

# ---------------------------------------------------------------------------
# ITU-R BS.1770-4 constants
# ---------------------------------------------------------------------------

MEASURE_RATE = 48_000

# Stage 1: shelving filter approximating the acoustic effect of the head.
_K_STAGE1_B = np.array([1.53512485958697, -2.69169618940638, 1.19839281085285])
_K_STAGE1_A = np.array([1.0, -1.69065929318241, 0.73248077421585])
# Stage 2: RLB high-pass.
_K_STAGE2_B = np.array([1.0, -2.0, 1.0])
_K_STAGE2_A = np.array([1.0, -1.99004745483398, 0.99007225036621])

_ABSOLUTE_GATE_LUFS = -70.0
_RELATIVE_GATE_LU = -10.0
_LUFS_OFFSET = -0.691  # so that a 1 kHz sine at -20 dBFS reads -20 LUFS

# Channel weights G_i from the standard. Stereo uses the first two.
_CHANNEL_WEIGHTS = np.array([1.0, 1.0, 1.0, 1.41, 1.41])


# ---------------------------------------------------------------------------
# I/O. Internally audio is float64 shaped (samples, channels).
# ---------------------------------------------------------------------------


def read_audio(path: str) -> tuple[np.ndarray, int]:
    """Read a soundfile into float64 (samples, channels)."""
    data, rate = sf.read(path, always_2d=True, dtype="float64")
    return data, int(rate)


def write_audio(path: str, data: np.ndarray, rate: int, subtype: str = "PCM_24") -> None:
    """Write (samples, channels) audio. 24-bit PCM by default: a master should
    not be delivered as 16-bit, and float WAV confuses some players."""
    if data.ndim == 1:
        data = data[:, None]
    sf.write(path, data, rate, subtype=subtype)


def resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Polyphase resample, preserving the (samples, channels) shape."""
    if src_rate == dst_rate:
        return data
    g = math.gcd(int(src_rate), int(dst_rate))
    up, down = dst_rate // g, src_rate // g
    return signal.resample_poly(data, up, down, axis=0)


# ---------------------------------------------------------------------------
# Loudness and peak measurement
# ---------------------------------------------------------------------------


def _k_weight(data: np.ndarray) -> np.ndarray:
    """Apply the two BS.1770 pre-filters. Input must be at 48 kHz."""
    out = signal.lfilter(_K_STAGE1_B, _K_STAGE1_A, data, axis=0)
    return signal.lfilter(_K_STAGE2_B, _K_STAGE2_A, out, axis=0)


def _block_mean_squares(weighted: np.ndarray, rate: int, block_s: float, overlap: float) -> np.ndarray:
    """Mean square per block per channel -> (blocks, channels)."""
    block = round(block_s * rate)
    step = max(1, round(block * (1.0 - overlap)))
    n = weighted.shape[0]
    if n < block:
        return np.empty((0, weighted.shape[1]))
    starts = range(0, n - block + 1, step)
    return np.stack([np.mean(weighted[s : s + block] ** 2, axis=0) for s in starts])


def _blocks_to_loudness(mean_squares: np.ndarray) -> np.ndarray:
    """Per-block loudness in LUFS from per-channel mean squares."""
    weights = _CHANNEL_WEIGHTS[: mean_squares.shape[1]]
    summed = mean_squares @ weights
    with np.errstate(divide="ignore"):
        return _LUFS_OFFSET + 10.0 * np.log10(np.maximum(summed, 1e-30))


def integrated_loudness(data: np.ndarray, rate: int) -> float:
    """Gated integrated loudness in LUFS, per BS.1770-4.

    Two-stage gating: an absolute -70 LUFS gate, then a gate 10 LU below the
    ungated mean of what survived. Without the gating a track with quiet
    passages measures far lower than it sounds, which is the whole reason the
    standard specifies it.
    """
    d = resample(data, rate, MEASURE_RATE)
    weighted = _k_weight(d)
    ms = _block_mean_squares(weighted, MEASURE_RATE, 0.400, 0.75)
    if ms.shape[0] == 0:
        return float("-inf")
    loud = _blocks_to_loudness(ms)

    above_absolute = loud > _ABSOLUTE_GATE_LUFS
    if not np.any(above_absolute):
        return float("-inf")

    weights = _CHANNEL_WEIGHTS[: ms.shape[1]]
    relative_ref = _LUFS_OFFSET + 10.0 * np.log10(max(float(np.mean(ms[above_absolute] @ weights)), 1e-30))
    keep = above_absolute & (loud > relative_ref + _RELATIVE_GATE_LU)
    if not np.any(keep):
        return float("-inf")
    return float(_LUFS_OFFSET + 10.0 * np.log10(max(float(np.mean(ms[keep] @ weights)), 1e-30)))


def loudness_range(data: np.ndarray, rate: int) -> float:
    """Loudness range (LRA) in LU, per EBU Tech 3342: 3 s blocks, a -20 LU
    relative gate, then the span between the 10th and 95th percentiles."""
    d = resample(data, rate, MEASURE_RATE)
    weighted = _k_weight(d)
    ms = _block_mean_squares(weighted, MEASURE_RATE, 3.0, 2.0 / 3.0)
    if ms.shape[0] < 2:
        return 0.0
    loud = _blocks_to_loudness(ms)
    above_absolute = loud > _ABSOLUTE_GATE_LUFS
    if not np.any(above_absolute):
        return 0.0
    weights = _CHANNEL_WEIGHTS[: ms.shape[1]]
    ref = _LUFS_OFFSET + 10.0 * np.log10(max(float(np.mean(ms[above_absolute] @ weights)), 1e-30))
    kept = loud[above_absolute & (loud > ref - 20.0)]
    if kept.size < 2:
        return 0.0
    return float(np.percentile(kept, 95) - np.percentile(kept, 10))


def true_peak_dbtp(data: np.ndarray, rate: int, oversample: int = 4) -> float:
    """True peak in dBTP.

    Sample peak misses inter-sample peaks, which is exactly what a streaming
    platform's decoder will reconstruct and then clip. BS.1770 calls for at
    least 4x oversampling before taking the maximum.
    """
    up = signal.resample_poly(data, oversample, 1, axis=0)
    peak = float(np.max(np.abs(up))) if up.size else 0.0
    return 20.0 * math.log10(peak) if peak > 0 else float("-inf")


def sample_peak_dbfs(data: np.ndarray) -> float:
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    return 20.0 * math.log10(peak) if peak > 0 else float("-inf")


def clipped_runs(data: np.ndarray, threshold: float = 0.9995, min_run: int = 3) -> int:
    """Count runs of consecutive samples pinned at full scale.

    A single sample at full scale is unremarkable; three or more in a row is the
    signature of something that was already clipped before it reached us.
    """
    mono = np.max(np.abs(data), axis=1)
    hot = mono >= threshold
    if not np.any(hot):
        return 0
    edges = np.diff(np.concatenate(([0], hot.view(np.int8), [0])))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return int(np.sum((ends - starts) >= min_run))


def dc_offset(data: np.ndarray) -> float:
    return float(np.max(np.abs(np.mean(data, axis=0)))) if data.size else 0.0


def stereo_correlation(data: np.ndarray) -> float | None:
    """Correlation between channels: ~1.0 is near-mono, negative risks
    cancellation when a listener's playback folds to mono."""
    if data.shape[1] < 2:
        return None
    left, right = data[:, 0], data[:, 1]
    if np.std(left) < 1e-9 or np.std(right) < 1e-9:
        return None
    return float(np.corrcoef(left, right)[0, 1])


@dataclass
class Measurements:
    """Everything measurable about a rendered file, as data."""

    duration_s: float
    sample_rate: int
    channels: int
    integrated_lufs: float
    loudness_range_lu: float
    true_peak_dbtp: float
    sample_peak_dbfs: float
    clipped_runs: int
    dc_offset: float
    stereo_correlation: float | None
    silent: bool

    def to_dict(self) -> dict:
        d = asdict(self)
        # -inf is valid for digital silence but is not JSON, so report it as None.
        for k, v in d.items():
            if isinstance(v, float) and not math.isfinite(v):
                d[k] = None
        return d


def measure(path: str) -> Measurements:
    data, rate = read_audio(path)
    return Measurements(
        duration_s=round(data.shape[0] / rate, 3),
        sample_rate=rate,
        channels=int(data.shape[1]),
        integrated_lufs=round(integrated_loudness(data, rate), 2),
        loudness_range_lu=round(loudness_range(data, rate), 2),
        true_peak_dbtp=round(true_peak_dbtp(data, rate), 2),
        sample_peak_dbfs=round(sample_peak_dbfs(data), 2),
        clipped_runs=clipped_runs(data),
        dc_offset=round(dc_offset(data), 6),
        stereo_correlation=(round(c, 4) if (c := stereo_correlation(data)) is not None else None),
        silent=bool(np.max(np.abs(data)) < 1e-5) if data.size else True,
    )


# ---------------------------------------------------------------------------
# Filters. RBJ Audio EQ Cookbook biquads, returned as second-order sections.
# ---------------------------------------------------------------------------


def _biquad(kind: str, freq: float, rate: float, q: float = 0.707, gain_db: float = 0.0) -> np.ndarray:
    w0 = 2.0 * math.pi * max(min(freq, rate * 0.49), 1.0) / rate
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    alpha = sin_w0 / (2.0 * max(q, 1e-3))
    A = 10.0 ** (gain_db / 40.0)

    if kind == "highpass":
        b = [(1 + cos_w0) / 2, -(1 + cos_w0), (1 + cos_w0) / 2]
        a = [1 + alpha, -2 * cos_w0, 1 - alpha]
    elif kind == "lowpass":
        b = [(1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2]
        a = [1 + alpha, -2 * cos_w0, 1 - alpha]
    elif kind == "peaking":
        b = [1 + alpha * A, -2 * cos_w0, 1 - alpha * A]
        a = [1 + alpha / A, -2 * cos_w0, 1 - alpha / A]
    elif kind in ("lowshelf", "highshelf"):
        sq = 2.0 * math.sqrt(A) * alpha
        if kind == "lowshelf":
            b = [
                A * ((A + 1) - (A - 1) * cos_w0 + sq),
                2 * A * ((A - 1) - (A + 1) * cos_w0),
                A * ((A + 1) - (A - 1) * cos_w0 - sq),
            ]
            a = [(A + 1) + (A - 1) * cos_w0 + sq, -2 * ((A - 1) + (A + 1) * cos_w0), (A + 1) + (A - 1) * cos_w0 - sq]
        else:
            b = [
                A * ((A + 1) + (A - 1) * cos_w0 + sq),
                -2 * A * ((A - 1) + (A + 1) * cos_w0),
                A * ((A + 1) + (A - 1) * cos_w0 - sq),
            ]
            a = [(A + 1) - (A - 1) * cos_w0 + sq, 2 * ((A - 1) - (A + 1) * cos_w0), (A + 1) - (A - 1) * cos_w0 - sq]
    else:
        raise ValueError(f"unknown filter kind {kind!r}")

    b = np.array(b, dtype=np.float64) / a[0]
    a = np.array(a, dtype=np.float64) / a[0]
    return np.concatenate([b, a])[None, :]


def apply_filters(data: np.ndarray, rate: int, bands: list[dict]) -> np.ndarray:
    """Apply a list of ``{type, freq_hz, gain_db, q}`` bands in series.

    Uses sosfilt rather than sosfiltfilt: a mastering chain is causal, and
    zero-phase filtering would smear transients backwards in time.
    """
    if not bands:
        return data
    sos = np.vstack(
        [
            _biquad(
                b.get("type", "peaking"),
                float(b.get("freq_hz", 1000.0)),
                rate,
                float(b.get("q", 0.707)),
                float(b.get("gain_db", 0.0)),
            )
            for b in bands
        ]
    )
    return signal.sosfilt(sos, data, axis=0)


# ---------------------------------------------------------------------------
# Dynamics
# ---------------------------------------------------------------------------


def _smooth_envelope(level_db: np.ndarray, rate: int, attack_ms: float, release_ms: float) -> np.ndarray:
    """One-pole attack/release follower over a dB-domain level signal."""
    att = math.exp(-1.0 / max(attack_ms * 1e-3 * rate, 1.0))
    rel = math.exp(-1.0 / max(release_ms * 1e-3 * rate, 1.0))
    out = np.empty_like(level_db)
    prev = level_db[0] if level_db.size else 0.0
    for i, v in enumerate(level_db):
        coeff = att if v > prev else rel
        prev = coeff * prev + (1.0 - coeff) * v
        out[i] = prev
    return out


def compress(
    data: np.ndarray,
    rate: int,
    threshold_db: float = -18.0,
    ratio: float = 2.0,
    attack_ms: float = 20.0,
    release_ms: float = 200.0,
    knee_db: float = 6.0,
    makeup_db: float | None = None,
) -> tuple[np.ndarray, dict]:
    """Feed-forward peak compressor with a soft knee.

    Gain reduction is computed from the linked maximum across channels so the
    stereo image is not pulled around by one channel ducking alone.
    """
    if data.size == 0:
        return data, {"max_gain_reduction_db": 0.0}
    detector = np.max(np.abs(data), axis=1)
    with np.errstate(divide="ignore"):
        level_db = 20.0 * np.log10(np.maximum(detector, 1e-12))

    over = level_db - threshold_db
    target_reduction = np.zeros_like(over)
    if knee_db > 0:
        in_knee = (over > -knee_db / 2) & (over <= knee_db / 2)
        above = over > knee_db / 2
        target_reduction[in_knee] = (1.0 / ratio - 1.0) * (over[in_knee] + knee_db / 2) ** 2 / (2.0 * knee_db)
        target_reduction[above] = (1.0 / ratio - 1.0) * over[above]
    else:
        above = over > 0
        target_reduction[above] = (1.0 / ratio - 1.0) * over[above]

    smoothed = _smooth_envelope(-target_reduction, rate, attack_ms, release_ms)
    gain = 10.0 ** (-smoothed / 20.0)
    out = data * gain[:, None]
    if makeup_db:
        out = out * (10.0 ** (makeup_db / 20.0))
    return out, {"max_gain_reduction_db": round(float(np.max(smoothed)), 2)}


def limit(
    data: np.ndarray,
    rate: int,
    ceiling_dbtp: float = -1.0,
    lookahead_ms: float = 5.0,
    release_ms: float = 50.0,
    oversample: int = 4,
) -> tuple[np.ndarray, dict]:
    """Look-ahead true-peak limiter.

    The gain envelope is derived from the 4x oversampled signal, because the
    ceiling that matters is the true peak a decoder reconstructs, not the sample
    peak we happen to store. Look-ahead means the gain is already down by the
    time the transient arrives, rather than clamping it after the fact.
    """
    if data.size == 0:
        return data, {"max_gain_reduction_db": 0.0}
    ceiling = 10.0 ** (ceiling_dbtp / 20.0)

    up = signal.resample_poly(data, oversample, 1, axis=0)
    peak_up = np.max(np.abs(up), axis=1)
    # Fold the oversampled envelope back to the base rate, keeping the worst
    # case in each original sample period.
    usable = (peak_up.size // oversample) * oversample
    peak = peak_up[:usable].reshape(-1, oversample).max(axis=1)
    if peak.size < data.shape[0]:
        peak = np.concatenate([peak, np.repeat(peak[-1:], data.shape[0] - peak.size)])
    peak = peak[: data.shape[0]]

    needed = np.minimum(1.0, ceiling / np.maximum(peak, 1e-12))
    look = max(1, round(lookahead_ms * 1e-3 * rate))
    # Running minimum over the look-ahead window: pull gain down early.
    padded = np.concatenate([needed, np.repeat(needed[-1:], look)])
    strides = np.lib.stride_tricks.sliding_window_view(padded, look + 1)
    target = strides.min(axis=1)[: needed.size]

    rel = math.exp(-1.0 / max(release_ms * 1e-3 * rate, 1.0))
    gain = np.empty_like(target)
    prev = 1.0
    for i, t in enumerate(target):
        prev = t if t < prev else rel * prev + (1.0 - rel) * t
        gain[i] = prev

    out = data * gain[:, None]
    with np.errstate(divide="ignore"):
        reduction = -20.0 * np.log10(np.maximum(gain.min(), 1e-12))
    return out, {"max_gain_reduction_db": round(float(reduction), 2)}


def normalise_loudness(
    data: np.ndarray, rate: int, target_lufs: float, max_gain_db: float = 24.0
) -> tuple[np.ndarray, dict]:
    """Apply a single broadband gain so integrated loudness hits the target."""
    current = integrated_loudness(data, rate)
    if not math.isfinite(current):
        return data, {"applied_gain_db": 0.0, "measured_before_lufs": None}
    gain_db = float(np.clip(target_lufs - current, -max_gain_db, max_gain_db))
    return data * (10.0 ** (gain_db / 20.0)), {
        "applied_gain_db": round(gain_db, 2),
        "measured_before_lufs": round(current, 2),
    }


# ---------------------------------------------------------------------------
# Similarity features, for the compliance agent
# ---------------------------------------------------------------------------


def _chroma_filterbank(n_fft: int, rate: int) -> np.ndarray:
    """Map FFT bins onto 12 pitch classes.

    Each bin is assigned to the pitch class of its centre frequency, which is
    crude next to a constant-Q transform but needs no extra dependency and is
    sufficient to compare harmonic content between two renders.
    """
    freqs = np.fft.rfftfreq(n_fft, 1.0 / rate)
    bank = np.zeros((12, freqs.size))
    with np.errstate(divide="ignore", invalid="ignore"):
        midi = 69.0 + 12.0 * np.log2(np.maximum(freqs, 1e-9) / 440.0)
    valid = (freqs > 55.0) & (freqs < 5000.0)
    pitch_class = np.mod(np.round(midi).astype(int), 12)
    for b in np.flatnonzero(valid):
        bank[pitch_class[b], b] = 1.0
    return bank


def chroma(path: str, hop_s: float = 0.1, n_fft: int = 4096) -> np.ndarray:
    """CENS-style chroma: (frames, 12), L2-normalised per frame.

    Normalising per frame makes the feature insensitive to level, so a louder
    master of the same material still matches.
    """
    data, rate = read_audio(path)
    mono = np.mean(data, axis=1)
    hop = max(1, round(hop_s * rate))
    if mono.size < n_fft:
        mono = np.pad(mono, (0, n_fft - mono.size))
    window = np.hanning(n_fft)
    frames = 1 + (mono.size - n_fft) // hop
    bank = _chroma_filterbank(n_fft, rate)
    out = np.empty((frames, 12))
    for i in range(frames):
        seg = mono[i * hop : i * hop + n_fft] * window
        mag = np.abs(np.fft.rfft(seg))
        v = bank @ mag
        norm = np.linalg.norm(v)
        out[i] = v / norm if norm > 1e-9 else 0.0
    return out


def chroma_dtw_distance(a: np.ndarray, b: np.ndarray, transpositions: bool = True) -> tuple[float, int]:
    """Normalised DTW distance between two chromagrams, and the best rotation.

    Trying all twelve rotations of the pitch-class axis is what catches material
    that was transposed rather than copied verbatim, which is the case a plain
    fingerprint match misses entirely. Returns (distance in [0, 1], semitones).
    """
    if a.size == 0 or b.size == 0:
        return 1.0, 0
    best = (1.0, 0)
    for shift in range(12) if transpositions else (0,):
        rolled = np.roll(b, shift, axis=1)
        # Cosine distance matrix; both inputs are already L2-normalised.
        cost = 1.0 - (a @ rolled.T)
        n, m = cost.shape
        acc = np.full((n + 1, m + 1), np.inf)
        acc[0, 0] = 0.0
        for i in range(1, n + 1):
            row, prev = acc[i], acc[i - 1]
            c = cost[i - 1]
            for j in range(1, m + 1):
                row[j] = c[j - 1] + min(prev[j], row[j - 1], prev[j - 1])
        d = float(acc[n, m] / (n + m))
        if d < best[0]:
            best = (d, shift)
    return best


@dataclass
class SimilarityHit:
    reference: str
    distance: float
    semitone_shift: int
    similarity: float = field(init=False)

    def __post_init__(self) -> None:
        # Distance 0 is identical; report a friendlier 0-1 similarity too.
        self.similarity = round(max(0.0, 1.0 - self.distance), 4)

    def to_dict(self) -> dict:
        return {
            "reference": self.reference,
            "distance": round(self.distance, 4),
            "semitone_shift": self.semitone_shift,
            "similarity": self.similarity,
        }
