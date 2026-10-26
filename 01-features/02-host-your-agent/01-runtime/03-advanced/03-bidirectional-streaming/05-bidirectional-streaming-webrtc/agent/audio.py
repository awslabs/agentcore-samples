"""Audio conversion and output track for WebRTC <-> Nova Sonic.

Nova Sonic expects 16kHz/16-bit/mono PCM input and produces 24kHz/16-bit/mono
PCM output. This module handles the format conversions and provides a WebRTC
audio track that streams Nova Sonic responses back to the browser.
"""

import asyncio
import fractions
import time

import av
from aiortc.mediastreams import AudioFrame, MediaStreamTrack

# Audio format constants
INPUT_SAMPLE_RATE = 16000  # What Nova Sonic expects
OUTPUT_SAMPLE_RATE = 24000  # What Nova Sonic produces
BYTES_PER_SAMPLE = 2  # 16-bit PCM
FRAME_DURATION_MS = 20  # WebRTC frame size
SAMPLES_PER_FRAME = OUTPUT_SAMPLE_RATE * FRAME_DURATION_MS // 1000  # 480


def new_resampler():
    """Create a resampler that converts WebRTC input (typically 48kHz stereo) to Nova Sonic format.

    Build this per session rather than at import time. The runtime snapshots the container
    once at deploy time and restores it for each session, so a native FFmpeg object created
    at import would be frozen into that snapshot and restored in an unusable state --
    it then yields correctly sized frames containing silence. Creating it per session also
    avoids sharing resampler state across concurrent peer connections.
    """
    return av.AudioResampler(format="s16", layout="mono", rate=INPUT_SAMPLE_RATE)


def new_silence_frame():
    """Create a silence frame for when no audio is available (see new_resampler for why per session)."""
    frame = AudioFrame(format="s16", layout="mono", samples=SAMPLES_PER_FRAME)
    frame.sample_rate = OUTPUT_SAMPLE_RATE
    frame.planes[0].update(bytes(SAMPLES_PER_FRAME * BYTES_PER_SAMPLE))
    return frame


def convert_to_16khz(frame, resampler):
    """Convert a WebRTC audio frame to 16kHz/16-bit/mono PCM bytes."""
    resampled = resampler.resample(frame)
    return b"".join(f.planes[0] for f in resampled) if resampled else b""


class OutputTrack(MediaStreamTrack):
    """WebRTC audio track that plays Nova Sonic responses to the browser.

    Audio bytes are queued via add_audio() into an av.AudioFifo, which
    handles chunking into exact frame sizes. recv() reads fixed-size
    frames paced to real-time, returning silence when the buffer is empty.
    """

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._fifo = av.AudioFifo()
        self._silence = new_silence_frame()  # per session, not at import -- see new_resampler
        self._start_time = None
        self._timestamp = 0
        self._muted = False

    async def recv(self):
        """Return the next 20ms audio frame, paced to real-time."""
        # Initialize timing on first call
        if self._start_time is None:
            self._start_time = time.time()
            self._frame_count = 0

        # Sleep until this frame's scheduled time (maintains 20ms cadence)
        delay = self._start_time + self._frame_count * (FRAME_DURATION_MS / 1000) - time.time()
        if delay > 0:
            await asyncio.sleep(delay)

        # Return silence if muted (barge-in) or buffer empty
        if self._muted:
            frame = self._silence
        else:
            frame = self._fifo.read(SAMPLES_PER_FRAME, partial=False)
            if frame is None:
                frame = self._silence

        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, OUTPUT_SAMPLE_RATE)
        self._timestamp += SAMPLES_PER_FRAME
        self._frame_count += 1
        return frame

    def clear(self):
        """Stop playback and discard all buffered audio (barge-in)."""
        self._muted = True
        self._fifo = av.AudioFifo()

    def add_audio(self, audio_bytes):
        """Buffer PCM bytes from Nova Sonic. AudioFifo handles chunking."""
        self._muted = False
        frame = AudioFrame(format="s16", layout="mono", samples=len(audio_bytes) // BYTES_PER_SAMPLE)
        frame.planes[0].update(audio_bytes)
        frame.sample_rate = OUTPUT_SAMPLE_RATE
        self._fifo.write(frame)
