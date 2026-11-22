# Offline plans only: mocks cannot verify the existing group/configuration in AWS.
mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

mock_provider "time" {}

variables {
  project     = "example"
  environment = "test"
  region      = "us-east-1"
  account_id  = "123456789012"
  name_prefix = "example_new"
}

run "default_creates_fresh_logging" {
  command = plan

  assert {
    condition = (
      length(aws_cloudwatch_log_group.invocations) == 1 &&
      length(aws_iam_role.invocation_logging) == 1 &&
      length(aws_iam_role_policy.write_logs) == 1 &&
      length(time_sleep.iam_propagation) == 1 &&
      length(aws_bedrock_model_invocation_logging_configuration.this) == 1
    )
    error_message = "Default mode must create every invocation-logging resource."
  }

  assert {
    condition = (
      aws_cloudwatch_log_group.invocations[0].name == "/aws/bedrock/example_new_invocations" &&
      aws_bedrock_model_invocation_logging_configuration.this[0].logging_config[0].cloudwatch_config[0].log_group_name == aws_cloudwatch_log_group.invocations[0].name &&
      aws_ssm_parameter.invocation_log_group.value == aws_cloudwatch_log_group.invocations[0].name &&
      output.invocation_log_group == aws_cloudwatch_log_group.invocations[0].name
    )
    error_message = "Default logging config, SSM pointer and output must refer to the new group."
  }

  assert {
    condition = (
      aws_xray_trace_segment_destination.this.destination == "CloudWatchLogs" &&
      aws_xray_indexing_rule.default.rule[0].probabilistic[0].desired_sampling_percentage == 100
    )
    error_message = "Default mode must send spans to CloudWatch Logs and index 100% of them."
  }
}

run "reuse_has_zero_logging_ownership" {
  command = plan

  variables {
    existing_invocation_log_group = "/aws/bedrock/existing_example_invocations"
  }

  assert {
    condition = (
      length(aws_cloudwatch_log_group.invocations) == 0 &&
      length(aws_iam_role.invocation_logging) == 0 &&
      length(aws_iam_role_policy.write_logs) == 0 &&
      length(time_sleep.iam_propagation) == 0 &&
      length(aws_bedrock_model_invocation_logging_configuration.this) == 0 &&
      length(data.aws_iam_policy_document.bedrock_trust) == 0 &&
      length(data.aws_iam_policy_document.write_logs) == 0
    )
    error_message = "Reuse mode must not own or construct Bedrock invocation-logging resources."
  }

  assert {
    condition = (
      aws_ssm_parameter.invocation_log_group.name == "/example/test/observability/invocation_log_group" &&
      aws_ssm_parameter.invocation_log_group.value == "/aws/bedrock/existing_example_invocations" &&
      output.invocation_log_group == "/aws/bedrock/existing_example_invocations"
    )
    error_message = "Reuse mode must publish the existing group through the same SSM parameter."
  }

  assert {
    condition = (
      aws_xray_trace_segment_destination.this.destination == "CloudWatchLogs" &&
      aws_xray_indexing_rule.default.rule[0].probabilistic[0].desired_sampling_percentage == 100 &&
      aws_cloudwatch_log_resource_policy.xray.policy_name == "example_new_transaction_search"
    )
    error_message = "Reuse mode must keep the X-Ray destination, indexing rule and resource policy."
  }
}

run "reject_empty" {
  command = plan
  variables { existing_invocation_log_group = "" }
  expect_failures = [var.existing_invocation_log_group]
}

run "reject_wrong_namespace" {
  command = plan
  variables { existing_invocation_log_group = "/aws/lambda/not-bedrock" }
  expect_failures = [var.existing_invocation_log_group]
}

run "reject_arn" {
  command = plan
  variables { existing_invocation_log_group = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/bedrock/example" }
  expect_failures = [var.existing_invocation_log_group]
}

run "reject_wildcard" {
  command = plan
  variables { existing_invocation_log_group = "/aws/bedrock/example*" }
  expect_failures = [var.existing_invocation_log_group]
}

run "reject_parent_traversal" {
  command = plan
  variables { existing_invocation_log_group = "/aws/bedrock/../example" }
  expect_failures = [var.existing_invocation_log_group]
}

run "reject_trailing_slash" {
  command = plan
  variables { existing_invocation_log_group = "/aws/bedrock/example/" }
  expect_failures = [var.existing_invocation_log_group]
}

run "reject_overlong" {
  command = plan
  variables { existing_invocation_log_group = "/aws/bedrock/${join("", [for i in range(500) : "a"])}" }
  expect_failures = [var.existing_invocation_log_group]
}
