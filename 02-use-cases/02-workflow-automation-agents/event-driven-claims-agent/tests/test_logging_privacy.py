"""Application log/status privacy, using real orchestration and Uvicorn config."""

import copy
import io
import logging
import logging.config
import unittest
from unittest.mock import patch

import test_pipeline as pipeline
from uvicorn.config import LOGGING_CONFIG


class LoggingPrivacyTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        pipeline.PipelineTests.setUpClass()
        cls.agent = pipeline.PipelineTests.agent

    async def test_transport_exception_is_not_streamed_or_logged(self):
        private = "private-upstream-response-sentinel"

        class BrokenGateway(pipeline.GatewayFixture):
            async def call_tool_async(self, **kwargs):
                raise RuntimeError(private)

        with self.assertLogs(self.agent.log.name, level="INFO") as captured:
            output, _ = await pipeline.PipelineTests.run_claim(self, gateway=BrokenGateway())
        self.assertIn("Could not create claim record", output)
        self.assertNotIn(private, output + "\n".join(captured.output))

    async def test_success_payload_is_not_in_status_logs(self):
        private = "private-tool-result-sentinel"
        results = [
            {"claim_id": "CLM-OFFLINE", "private_field": private},
            {"status": "suppressed", "private_field": private},
        ]
        with (
            patch.object(self.agent, "_call_tool", side_effect=results),
            self.assertLogs(self.agent.log.name, level="INFO") as captured,
        ):
            output, _ = await pipeline.PipelineTests.run_claim(self)
        self.assertIn("CLM-OFFLINE", output)
        self.assertNotIn(private, output + "\n".join(captured.output))

    def test_identity_exception_is_not_logged(self):
        private = "private-identity-response-sentinel"
        with (
            patch.object(self.agent, "_build_mcp_client", side_effect=RuntimeError(private)),
            self.assertLogs(self.agent.log.name, level="WARNING") as captured,
        ):
            self.assertIsNone(self.agent.get_mcp_client())
        self.assertNotIn(private, "\n".join(captured.output))

    def test_stream_logger_guard_survives_uvicorn_configuration(self):
        stream_logger = logging.getLogger("strands.event_loop.streaming")
        control = logging.getLogger("claims-privacy-capture-control")
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        original = (stream_logger.level, stream_logger.disabled, control.level)
        try:
            logging.config.dictConfig(copy.deepcopy(LOGGING_CONFIG))
            stream_logger.addHandler(handler)
            control.addHandler(handler)
            control.setLevel(logging.WARNING)
            control.warning("capture is active")
            stream_logger.warning("private-stream-event-sentinel")
            self.assertIn("capture is active", output.getvalue())
            self.assertNotIn("private-stream-event-sentinel", output.getvalue())
        finally:
            stream_logger.removeHandler(handler)
            control.removeHandler(handler)
            stream_logger.setLevel(original[0])
            stream_logger.disabled = original[1]
            control.setLevel(original[2])
            handler.close()
