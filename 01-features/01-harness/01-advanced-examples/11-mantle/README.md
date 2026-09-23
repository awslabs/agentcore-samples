# Run a harness on the Bedrock Mantle (OpenAI-compatible) endpoint

These examples run an AgentCore harness against the **Mantle** endpoint
(`bedrock-mantle.<region>.api.aws`), the OpenAI-compatible face of Amazon Bedrock. You select it on
the harness with `--api-format responses` (or `chat_completions`) instead of the default
`converse_stream`, which routes inference through `bedrock-mantle` rather than `bedrock-runtime`.

Two walkthroughs, smallest first:

- **[endpoint](endpoint)** — the open-weight **`gpt-oss-120b`** model with `--api-format responses`.
  The minimal "harness on Mantle" example: deploy, invoke, confirm spans in CloudWatch.
- **[gpt5](gpt5)** — the hosted **GPT-5.4** model on the same path. GPT-5.4 supports the Responses API
  only, so it must use `--api-format responses`. Builds on the endpoint example.

For the LiteLLM routing variant of the same Mantle path (a credential provider plus
`--model-provider lite_llm`), see **[../12-litellm-mantle](../12-litellm-mantle)**.

Both subfolders are self-contained: each has its own `demo.sh`, `cleanup.sh`, `README.md`, and
recording. Start with whichever model you have access to.
