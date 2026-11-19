"""Tests for the failed-session count shown by status.py."""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import status


class FakeCloudWatch:
    def __init__(self, counts):
        self.counts, self.calls = counts, []

    def get_metric_statistics(self, **kwargs):
        self.calls.append(kwargs)
        return {"Datapoints": [{"SampleCount": c, "Timestamp": datetime.now(UTC)} for c in self.counts]}


def test_sums_sample_counts_across_hourly_data_points():
    assert status.sessions_evaluated(FakeCloudWatch([12.0, 24.0, 0.0]), "App.DEFAULT", "Failures Found") == 36


def test_no_data_points_means_zero():
    assert status.sessions_evaluated(FakeCloudWatch([]), "App.DEFAULT", "Failures Found") == 0


def test_queries_the_failure_analysis_metric_for_this_application_and_label():
    cloudwatch = FakeCloudWatch([1.0])
    status.sessions_evaluated(cloudwatch, "App.DEFAULT", "No Failures Found")
    call = cloudwatch.calls[0]
    assert call["Namespace"] == "Bedrock-AgentCore/Evaluations"
    assert call["MetricName"] == "Builtin.FailureAnalysis"
    assert {"Name": "label", "Value": "No Failures Found"} in call["Dimensions"]
    assert {"Name": "service.name", "Value": "App.DEFAULT"} in call["Dimensions"]
    assert call["Statistics"] == ["SampleCount"]
