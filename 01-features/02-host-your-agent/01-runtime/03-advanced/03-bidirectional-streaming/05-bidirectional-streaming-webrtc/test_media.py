"""Exercise the actual audio and WebRTC stack without a microphone or cloud services."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioFrame
from fastapi import BackgroundTasks

sys.path.insert(0, str(Path(__file__).with_name("agent")))
import audio
import bot
import nova_sonic


def test_webrtc_offer_audio_and_disconnect():
    async def exchange():
        browser = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        browser.addTransceiver("audio", direction="recvonly")
        try:
            await browser.setLocalDescription(await browser.createOffer())
            with patch.object(bot.kvs, "get_rtc_ice_servers", return_value=[]):
                result = await bot._handle_offer(
                    {"sdp": browser.localDescription.sdp, "type": "offer"}, BackgroundTasks()
                )
            await browser.setRemoteDescription(RTCSessionDescription(sdp=result["sdp"], type="answer"))
            track = browser.getReceivers()[0].track
            frame = await asyncio.wait_for(track.recv(), timeout=10)
            assert frame.samples > 0
            assert await bot._handle_disconnect({"pc_id": result["pc_id"]}) == {"status": "success"}
            assert result["pc_id"] not in bot.peer_connections
        finally:
            await browser.close()
            for pc in bot.peer_connections.values():
                await pc.close()
            bot.peer_connections.clear()

    # ICE normally excludes loopback. Keep real DTLS/SRTP packets inside the test host.
    with patch("aioice.ice.get_host_addresses", return_value=["127.0.0.1"]):
        asyncio.run(exchange())


def test_non_silent_pcm_and_smithy_event():
    async def convert():
        frame = AudioFrame(format="s16", layout="mono", samples=960)
        frame.sample_rate = 48000
        frame.planes[0].update(b"\x10\x01" * 960)
        pcm = audio.convert_to_16khz(frame, audio.new_resampler())
        assert pcm and any(pcm)
        track = audio.OutputTrack()
        track.add_audio(b"\x10\x01" * audio.SAMPLES_PER_FRAME)
        out = await track.recv()
        assert out.sample_rate == 24000 and out.samples == 480 and any(bytes(out.planes[0]))
        stream = Mock()
        stream.input_stream.send = AsyncMock()
        await nova_sonic._setup_session(stream, "sample-prompt")
        events = [json.loads(call.args[0].value.bytes_)["event"] for call in stream.input_stream.send.await_args_list]
        assert "sessionStart" in events[0] and any("promptStart" in e for e in events)

    asyncio.run(convert())


def test_duplex_sdk_setup(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fixture")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fixture")

    async def check():
        # Only replace the operation that would transmit to Bedrock. Config, credential chain,
        # HTTP/2 transport and client/stream context management use the installed SDK contract.
        stream = AsyncMock()
        with (
            patch.object(
                nova_sonic.AsyncBedrockRuntimeClient,
                "invoke_model_with_bidirectional_stream",
                new_callable=AsyncMock,
                return_value=stream,
            ) as invoke,
            patch.object(nova_sonic, "exchange_audio", new_callable=AsyncMock) as exchange,
        ):
            await nova_sonic.run_session(None, None, "us-west-2", "fixture")
            assert invoke.await_args.args[0].model_id == nova_sonic.MODEL_ID
            exchange.assert_awaited_once()
            stream.__aexit__.assert_awaited_once()

    asyncio.run(check())
