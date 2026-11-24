"""Routine console/log privacy; traces and model capture need separate governance."""

import asyncio
import contextlib
import copy
import io
import logging
import logging.config
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import test_offline as fixtures
from uvicorn.config import LOGGING_CONFIG


class LoggingPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.app = fixtures.load("privacy_sample_app", fixtures.AGENT / "app.py")

    def test_prompt_load_error_does_not_print_upstream_details(self):
        private = "private-file-error-sentinel"
        output = io.StringIO()
        with (
            patch.object(self.app, "load_file_content", side_effect=OSError(private)),
            contextlib.redirect_stdout(output),
        ):
            self.assertTrue(self.app.load_system_prompt())
        self.assertNotIn(private, output.getvalue())

    def test_memory_close_error_and_actor_are_not_console_output(self):
        private = "private-memory-error-sentinel"
        actor = str(uuid4())
        manager = MagicMock()
        manager.close.side_effect = RuntimeError(private)
        mcp = MagicMock()
        mcp.list_tools_sync.return_value = []
        agent = MagicMock()

        async def stream(_prompt):
            yield {"data": "offline answer"}

        agent.stream_async.side_effect = stream
        output = io.StringIO()
        with (
            patch.object(self.app, "authenticated_actor", return_value=actor),
            patch.object(self.app, "QueryResults"),
            patch.object(self.app, "BedrockModel"),
            patch.object(self.app, "AgentCoreMemoryConfig"),
            patch.object(self.app, "AgentCoreMemorySessionManager", return_value=manager),
            patch.object(self.app, "get_mcp_client", return_value=mcp),
            patch.object(self.app, "Agent", return_value=agent),
            contextlib.redirect_stdout(output),
        ):

            async def collect():
                payload = {"prompt": "offline", "session_id": str(uuid4()), "prompt_uuid": str(uuid4())}
                return "".join([part async for part in self.app.agent_invocation(payload)])

            response = asyncio.run(collect())
        manager.close.assert_called_once()
        self.assertIn("offline answer", response)
        self.assertNotIn(private, output.getvalue() + response)
        self.assertNotIn(actor, output.getvalue())

    def test_stream_logger_guard_survives_uvicorn_configuration(self):
        stream = logging.getLogger("strands.event_loop.streaming")
        control = logging.getLogger("data-privacy-capture-control")
        original = (stream.level, stream.disabled, control.level)
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        try:
            logging.config.dictConfig(copy.deepcopy(LOGGING_CONFIG))
            stream.addHandler(handler)
            control.addHandler(handler)
            control.setLevel(logging.WARNING)
            control.warning("capture is active")
            stream.warning("private-stream-event-sentinel")
            self.assertIn("capture is active", output.getvalue())
            self.assertNotIn("private-stream-event-sentinel", output.getvalue())
        finally:
            stream.removeHandler(handler)
            control.removeHandler(handler)
            stream.setLevel(original[0])
            stream.disabled = original[1]
            control.setLevel(original[2])
            handler.close()
