output "evals" {
  value = {
    runner_function  = aws_lambda_function.runner.function_name
    online_config_id = aws_bedrockagentcore_online_evaluation_config.agent.online_evaluation_config_id
    evaluators = {
      no_forbidden_effect = aws_bedrockagentcore_evaluator.no_forbidden_effect.evaluator_id
      ticket_text_as_data = aws_bedrockagentcore_evaluator.ticket_text_as_data.evaluator_id
    }
  }
}
