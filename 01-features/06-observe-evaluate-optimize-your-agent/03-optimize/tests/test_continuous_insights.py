"""Unit tests for the additive Continuous Insights workflow."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_ROOT))

import continuous_insights
import generate_insight_sessions
from utils.prompt_override import apply_system_prompt, apply_system_prompt_file
from utils.quality_checks import quality_gate_failures, summarize_records
from utils.response_stream import decode_agentcore_response


class FakeBody:
    """Minimal body implementing the methods used by the decoder."""

    def __init__(self, value: bytes):
        self.value = value

    def read(self):
        return self.value

    def iter_lines(self):
        return self.value.splitlines()


class ContinuousInsightsTests(unittest.TestCase):
    """Validate deterministic generation and safe exports."""

    def test_insights_preset_contains_54_sessions(self):
        workload = generate_insight_sessions.build_workload("insights")
        self.assertEqual(len(workload), 54)
        counts = {name: sum(item.name == name for item in workload) for name in generate_insight_sessions.SCENARIOS}
        self.assertEqual(
            counts,
            {
                "success": 4,
                "unknown_employee": 13,
                "unknown_pay_stub": 13,
                "unsupported_policy": 12,
                "unsupported_benefit": 12,
            },
        )

    def test_export_ids_are_deduplicated_and_path_safe(self):
        self.assertEqual(
            continuous_insights.validate_export_insight_ids(["one", "two", "one"]),
            ["one", "two"],
        )
        with self.assertRaisesRegex(ValueError, "only letters"):
            continuous_insights.validate_export_insight_ids(["../unsafe"])

    def test_task_contains_service_fix_and_evidence(self):
        task = continuous_insights.render_coding_assistant_task(
            application_name="HRAssist.DEFAULT",
            insights=[
                {
                    "agentInsightId": "abc123",
                    "name": "Fabricated constraint",
                    "description": "The response invented supported IDs.",
                    "rootCauses": [
                        {
                            "name": "Ungrounded explanation",
                            "explanation": "The agent filled in missing details.",
                            "suggestedFix": "Only report facts returned by tools.",
                            "sampleSessions": [
                                {
                                    "sessionId": "session-1",
                                    "findings": [
                                        {
                                            "targetType": "SPAN_ID",
                                            "targetId": "span-1",
                                            "description": "Invented an employee list.",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        )
        self.assertIn("Only report facts returned by tools.", task)
        self.assertIn("Invented an employee list.", task)
        self.assertIn("do not deploy automatically", task)

    def test_sse_decoder_removes_internal_reasoning(self):
        body = FakeBody(b'data: "<thinking"\ndata: ">"\ndata: "private"\ndata: "</thinking>"\ndata: "Public answer"\n')
        self.assertEqual(
            decode_agentcore_response({"contentType": "text/event-stream", "response": body}),
            "Public answer",
        )

    def test_grounded_failure_responses_pass(self):
        summary = summarize_records(
            [
                {
                    "scenario": "unknown_employee",
                    "delivery_status": "delivered",
                    "response_summary": ("Employee EMP-999 was not found. Please contact HR."),
                },
                {
                    "scenario": "unknown_pay_stub",
                    "delivery_status": "delivered",
                    "response_summary": ("That pay stub was not found. Please contact Payroll."),
                },
                {
                    "scenario": "unknown_employee",
                    "delivery_status": "delivered",
                    "response_summary": ("The system does not recognize the provided employee ID. Please contact HR."),
                },
                {
                    "scenario": "unknown_employee",
                    "delivery_status": "delivered",
                    "response_summary": ("The system did not find employee EMP-999. Please contact HR."),
                },
                {
                    "scenario": "unknown_pay_stub",
                    "delivery_status": "delivered",
                    "response_summary": ("The pay stub for EMP-003 could not be retrieved. Please contact Payroll."),
                },
                {
                    "scenario": "unknown_pay_stub",
                    "delivery_status": "delivered",
                    "response_summary": ("There was an issue retrieving your pay stub. Please contact Payroll."),
                },
            ]
        )
        self.assertEqual(quality_gate_failures(summary), [])

    def test_prompt_override_replaces_only_the_default_prompt(self):
        source = 'DEFAULT_SYSTEM_PROMPT = """Original prompt"""\nOTHER_VALUE = """Preserved value"""\n'

        updated = apply_system_prompt(source, "Ground every response in tools.")

        self.assertIn("Ground every response in tools.", updated)
        self.assertNotIn("Original prompt", updated)
        self.assertIn('OTHER_VALUE = """Preserved value"""', updated)

    def test_prompt_override_rejects_empty_input(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            apply_system_prompt('DEFAULT_SYSTEM_PROMPT = """Original"""', " ")

    def test_omitted_prompt_file_preserves_source_exactly(self):
        source = 'DEFAULT_SYSTEM_PROMPT = """Original"""\n'
        self.assertIs(apply_system_prompt_file(source, None), source)

    def test_prompt_file_is_applied_at_build_time(self):
        source = 'DEFAULT_SYSTEM_PROMPT = """Original"""\n'
        with TemporaryDirectory() as directory:
            path = Path(directory) / "prompt.txt"
            path.write_text("Ground responses in tools.", encoding="utf-8")
            updated = apply_system_prompt_file(source, path)
        self.assertIn("Ground responses in tools.", updated)

    def test_gate_rejects_fabricated_employee_ids(self):
        summary = summarize_records(
            [
                {
                    "scenario": "unknown_employee",
                    "delivery_status": "delivered",
                    "response_summary": ("Employee EMP-999 was not found. Valid ID: EMP-777."),
                }
            ]
        )
        self.assertIn(
            "grounded_unknown_employee",
            quality_gate_failures(summary),
        )


if __name__ == "__main__":
    unittest.main()
