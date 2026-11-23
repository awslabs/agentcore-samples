# Evaluate an AgentCore Runtime with Langfuse

Run a small, repeatable experiment against **one existing DEV or TST Runtime**. A Langfuse experiment creates the parent observation for each dataset question; the host passes its trace ID and observation ID in a signed `InvokeAgentRuntime` request. The [agent](agents/strands_claude.py) creates a child observation, calls the Langfuse documentation MCP server and answers using Bedrock. The host grades the answer against the reviewed reference phrases in [dataset.json](dataset.json). Deploying, promoting and deleting infrastructure are not evaluation steps.

## Prepare the sandbox

- Have the Runtime owner deploy the supplied [container](Dockerfile) through their approved workflow. The Runtime requires access to the selected Bedrock model and outbound HTTPS to the Langfuse documentation MCP server and your approved Langfuse origin. Select an available model with `BEDROCK_MODEL_ID` and configure `LANGFUSE_TRACING_ENVIRONMENT=DEV` or `TST`. Its entrypoint accepts `prompt`, `trace_id`, and `parent_obs_id` and returns a JSON string. An arbitrary chat Runtime may not satisfy that contract.
- Configure the same **sandbox Langfuse project** for host and Runtime. For the agent, supply `LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` via your organization's approved configuration/secret mechanism. Never put keys in a Docker image, stack template or command line. The host takes the trusted HTTPS origin explicitly; don't use an origin sent by an agent or dataset. Use an isolated environment without unrelated OTLP exporters.
- Use a temporary AWS profile authorized only for the chosen Runtime's invocation and STS caller identity. No deployment role, model API key, repository deletion, resource discovery or SSM writes are needed on the experiment host. The helper supports commercial AWS and IAM-authenticated invocation.
- **Data and cost:** questions, answers, evaluator scores and traces go to the configured Langfuse project. Model and tool calls can incur charges. Use synthetic data only and review the project's retention/access settings. The included references are examples, not current Langfuse product guarantees.

## Run one experiment

From this directory, set the Langfuse keys in your host's secret environment and supply the actual account, Runtime ARN and trusted origin:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python evaluate.py --account "$ACCOUNT" --runtime-arn "$RUNTIME_ARN" \
  --langfuse-origin "$LANGFUSE_ORIGIN" --limit 1
```

`--limit` is 1–4 fixture questions, sequentially, with a fresh Runtime session per item. One Runtime invocation can itself perform multiple model or tool calls. The host does not rerun a failed experiment or silently deploy a replacement. Compare the traces across runs rather than treating a returned average as proof of export or authorization. For host-only offline tests, run `python -m unittest discover -s tests -p test_evaluate.py`.

The score is **literal reference-phrase coverage**, not a factuality judge. It can miss a correct paraphrase or reward a wrong answer that repeats a phrase. Review low and high scoring traces manually. Missing response bodies, incomplete result sets and missing scores are errors. Metadata fields for tool trajectory in the fixture are for human comparison; this evaluator does not score tool use. The trace is linked only when DEV/TST and host credentials refer to the same project.

## What changed and cleanup

This workflow creates experiment data, not AWS infrastructure. Clean up runs according to the Langfuse project owner's retention policy. Keep externally owned runtimes, models, roles and registries intact. The [historical walkthrough](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/03-integrations/AgentOps-Langfuse) also automated a hyperparameter grid, deployment, load simulation, a remote judge and CI promotion/teardown. Those are not reproduced here. For existing deployments, verify exact resource IDs and ownership before cleanup; never delete an ECR repository or a same-name Runtime just because an experiment used it.
