"""Run the browser contract with the installed SDK and no provider connection."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import agent
from fastapi import WebSocketDisconnect
from strands.bidi.types.events import BidiAudioDeltaEvent, BidiTranscriptBlockEvent


class Voice(unittest.TestCase):
    def test_provider_constructors(self):
        for name, audio_in, audio_out in [
            ("amazon.nova-2-sonic-v1:0", 16000, 16000),
            ("gpt-realtime", 24000, 24000),
            ("gemini-fixture", 16000, 24000),
        ]:
            model = agent.model_for({"model_id": name, "api_key": "fixture"})
            spec = model.get_audio_config()
            self.assertEqual((spec["input"]["sample_rate"], spec["output"]["sample_rate"]), (audio_in, audio_out))

    def test_browser_bridge(self):
        ws = AsyncMock()
        ws.receive_json.side_effect = [
            {"type": "config"},
            {"type": "text_input", "text": "hello"},
            {"type": "bidi_audio_input", "format": "pcm", "audio": "AQI="},
        ]

        async def scripted_run(instance, inputs, outputs):
            self.assertEqual(await inputs[0](), "hello")
            delta = await inputs[0]()
            self.assertEqual(delta.to_dict()["audio_delta"]["source"]["bytes"], b"\x01\x02")
            await outputs[0](BidiAudioDeltaEvent("AQI=", "pcm", 16000, 1, "audio"))
            await outputs[0](BidiTranscriptBlockEvent("hello", "user", "text"))
            raise WebSocketDisconnect()

        with patch.object(agent.BidiAgent, "run", scripted_run):
            asyncio.run(agent.handle_websocket_session(ws))
        messages = [call.args[0] for call in ws.send_json.await_args_list]
        self.assertEqual(messages[1], {"type": "bidi_audio_stream", "audio": "AQI=", "sample_rate": 16000})
        self.assertEqual(messages[2], {"type": "bidi_transcript_stream", "text": "hello", "role": "user"})
