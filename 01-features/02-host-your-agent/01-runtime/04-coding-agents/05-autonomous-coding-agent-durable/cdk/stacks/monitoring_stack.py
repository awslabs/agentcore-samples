"""Monitoring stack: CloudWatch alarms + dashboard for the durable orchestrator.

The orchestrator is a Lambda Durable Function: it suspends at $0 compute between
steps, so per-invocation Duration is NOT a meaningful health signal (a multi-hour
ticket is split into many short replays). We therefore alarm on Errors and
Throttles, and chart invocations/errors/duration/concurrency on a dashboard.
"""
import aws_cdk as cdk
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_lambda as lambda_,
    aws_sns as sns,
)
from constructs import Construct


class MonitoringStack(cdk.Stack):
    """CloudWatch alarms (errors, throttles) + dashboard with Lambda metrics."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        lambda_fn: lambda_.IFunction,
        sns_topic: sns.ITopic,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        sns_action = cw_actions.SnsAction(sns_topic)

        # --- Alarm: Lambda errors > 0 ---
        error_metric = lambda_fn.metric_errors(period=cdk.Duration.minutes(5), statistic="Sum")
        error_alarm = cloudwatch.Alarm(
            self,
            "LambdaErrorsAlarm",
            alarm_name=f"{project}-orchestrator-errors",
            alarm_description="Durable orchestrator errors > 0 in 5 min window",
            metric=error_metric,
            threshold=0,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        error_alarm.add_alarm_action(sns_action)
        error_alarm.add_ok_action(sns_action)

        # --- Alarm: Lambda throttles > 0 ---
        throttle_metric = lambda_fn.metric_throttles(period=cdk.Duration.minutes(5), statistic="Sum")
        throttle_alarm = cloudwatch.Alarm(
            self,
            "LambdaThrottlesAlarm",
            alarm_name=f"{project}-orchestrator-throttles",
            alarm_description="Durable orchestrator throttles > 0 in 5 min window",
            metric=throttle_metric,
            threshold=0,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        throttle_alarm.add_alarm_action(sns_action)
        throttle_alarm.add_ok_action(sns_action)

        # --- Dashboard ---
        dashboard = cloudwatch.Dashboard(
            self, "OrchestratorDashboard", dashboard_name=f"{project}-orchestrator",
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Invocations",
                left=[lambda_fn.metric_invocations(period=cdk.Duration.minutes(5), statistic="Sum")],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="Errors",
                left=[error_metric],
                left_annotations=[cloudwatch.HorizontalAnnotation(
                    value=1, label="Alarm threshold", color="#ff0000")],
                width=12,
            ),
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Duration (ms, per replay)",
                left=[
                    lambda_fn.metric_duration(period=cdk.Duration.minutes(5), statistic="Average"),
                    lambda_fn.metric_duration(period=cdk.Duration.minutes(5), statistic="Maximum"),
                ],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="Concurrent Executions",
                left=[lambda_fn.metric("ConcurrentExecutions", period=cdk.Duration.minutes(5),
                                       statistic="Maximum")],
                width=12,
            ),
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Throttles",
                left=[throttle_metric],
                left_annotations=[cloudwatch.HorizontalAnnotation(
                    value=1, label="Alarm threshold", color="#ff0000")],
                width=12,
            ),
            cloudwatch.SingleValueWidget(
                title="Success Rate (last hour)",
                metrics=[cloudwatch.MathExpression(
                    expression="100 - (errors / invocations) * 100",
                    using_metrics={
                        "errors": lambda_fn.metric_errors(period=cdk.Duration.hours(1), statistic="Sum"),
                        "invocations": lambda_fn.metric_invocations(period=cdk.Duration.hours(1), statistic="Sum"),
                    },
                    label="Success %",
                    period=cdk.Duration.hours(1),
                )],
                width=12,
            ),
        )

        # --- Outputs ---
        cdk.CfnOutput(
            self,
            "DashboardUrl",
            value=(
                f"https://{cdk.Stack.of(self).region}.console.aws.amazon.com"
                f"/cloudwatch/home#dashboards:name={project}-orchestrator"
            ),
            export_name=f"{project}-dashboard-url",
        )
        cdk.CfnOutput(self, "ErrorAlarmArn", value=error_alarm.alarm_arn,
                      export_name=f"{project}-error-alarm-arn")
        cdk.CfnOutput(self, "ThrottleAlarmArn", value=throttle_alarm.alarm_arn,
                      export_name=f"{project}-throttle-alarm-arn")
