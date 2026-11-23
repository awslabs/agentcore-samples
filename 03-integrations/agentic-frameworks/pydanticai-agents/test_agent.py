import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch

import pydantic_bedrock_claude as example
from pydantic import ValidationError
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.test import TestModel


class RuntimeTests(unittest.TestCase):
    def test_real_tool_dispatch(self):
        agent = example.build_agent(TestModel(call_tools=["get_current_date", "get_weather"]))
        result = asyncio.run(agent.run("What are the date and demo weather?"))
        tools = {
            part.tool_name: part.content
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        }
        self.assertEqual(tools["get_weather"], "Sunny (simulated)")
        self.assertEqual(datetime.fromisoformat(tools["get_current_date"]).utcoffset().total_seconds(), 0)
        self.assertIsInstance(result.output, str)

    def test_contract(self):
        agent = example.build_agent(TestModel(call_tools=[], custom_output_text="fixture response"))
        with patch.object(example, "build_agent", return_value=agent) as factory:
            self.assertEqual(asyncio.run(example.pydantic_bedrock_claude_main({"prompt": "hello"})), "fixture response")
            with self.assertRaises(ValidationError):
                asyncio.run(example.pydantic_bedrock_claude_main({"prompt": ""}))
            factory.assert_called_once()
