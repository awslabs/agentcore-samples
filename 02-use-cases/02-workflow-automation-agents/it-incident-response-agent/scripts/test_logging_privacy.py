"""Offline error/logging regressions; no model or remote tool calls."""

import asyncio
import copy
import importlib
import io
import logging
import logging.config
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from uvicorn.config import LOGGING_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/ITIncidentAgent"))


class LoggingPrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = importlib.import_module("main")

    def test_failed_status_write_does_not_log_service_exception(self):
        private = "private-database-error-sentinel"
        with (
            patch.object(self.main, "TICKETS_TABLE", "OfflineTickets"),
            patch.object(self.main, "_ddb") as ddb,
            self.assertLogs(self.main.logger.name, level="ERROR") as logs,
        ):
            ddb.Table.return_value.update_item.side_effect = RuntimeError(private)
            self.main._fail_ticket("T-1", "synthetic failure")
        self.assertNotIn(private, "\n".join(logs.output))

    def test_dev_fallback_does_not_log_raw_initialization_errors(self):
        private = "private-model-or-tool-init-sentinel"
        with (
            patch.object(self.main, "TICKETS_TABLE", ""),
            patch.object(self.main, "get_all_mcp_clients_safe", return_value=([], [])),
            patch.object(self.main, "load_model"),
            patch.object(self.main, "get_memory_session_manager", return_value=None),
            patch.object(self.main, "Agent", side_effect=RuntimeError(private)) as agent,
            self.assertLogs(self.main.logger.name, level="WARNING") as logs,
        ):

            async def collect():
                return [
                    json
                    async for json in self.main.invoke(
                        {"prompt": "synthetic"}, types.SimpleNamespace(session_id="offline", user_id="offline")
                    )
                ]

            output = asyncio.run(collect())
        self.assertEqual(agent.call_count, 2)
        self.assertIn("Agent initialization failed", "".join(output))
        self.assertNotIn(private, "".join(output) + "\n".join(logs.output))

    def test_stream_logger_guard_survives_uvicorn_configuration(self):
        stream = logging.getLogger("strands.event_loop.streaming")
        control = logging.getLogger("it-privacy-capture-control")
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
