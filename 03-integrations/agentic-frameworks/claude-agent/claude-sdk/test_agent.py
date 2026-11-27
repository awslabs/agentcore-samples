import asyncio
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import agent
from claude_agent_sdk import ResultMessage
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from starlette.exceptions import HTTPException


class ClaudeRuntime(unittest.TestCase):
    def test_cli_options_disable_tools(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"BEDROCK_MODEL_ID": "fixture"}, clear=True):
            options = agent.options(root)
            # Inspect the installed SDK's actual translation; no model process is started.
            transport = SubprocessCLITransport(prompt="synthetic", options=options)
            transport._cli_path = transport._find_cli()  # resolve as connect() would, without sending a prompt
            cmd = transport._build_command()
            self.assertEqual(cmd[cmd.index("--tools") + 1], "")
            self.assertIn("--strict-mcp-config", cmd)
            self.assertIn("--setting-sources=", cmd)
            self.assertEqual(cmd[cmd.index("--max-turns") + 1], "1")
            self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
            self.assertTrue(Path(cmd[0]).is_file())
            self.assertEqual(options.env["CLAUDE_CODE_USE_BEDROCK"], "1")

    def test_response_and_workspace_cleanup(self):
        workspaces = []

        async def query(*, prompt, options):
            self.assertEqual(prompt, "synthetic")
            workspaces.append(Path(options.cwd))
            self.assertTrue(workspaces[-1].exists())
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fixture",
                result="fixture answer",
            )

        with patch.dict(os.environ, {"BEDROCK_MODEL_ID": "fixture"}, clear=True), patch.object(agent, "query", query):
            for _ in range(2):
                self.assertEqual(asyncio.run(agent.answer({"prompt": "synthetic"})), {"response": "fixture answer"})
        self.assertNotEqual(*workspaces)
        self.assertFalse(any(p.exists() for p in workspaces))

    def test_invalid_input_or_token(self):
        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fixture"}, clear=True),
            patch.object(agent, "query") as query,
        ):
            for prompt, code in [("", 400), ("synthetic", 500)]:
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(agent.answer({"prompt": prompt}))
                self.assertEqual(error.exception.status_code, code)
            query.assert_not_called()

    def test_failure_redacts_provider_detail(self):
        async def fail(**kwargs):
            raise RuntimeError("private fixture marker")
            yield

        with (
            patch.dict(os.environ, {"BEDROCK_MODEL_ID": "fixture"}, clear=True),
            patch.object(agent, "query", fail),
            self.assertRaises(HTTPException) as error,
        ):
            asyncio.run(agent.answer({"prompt": "synthetic"}))
        self.assertNotIn("private", error.exception.detail)
