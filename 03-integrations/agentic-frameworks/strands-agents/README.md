# Stream a Strands agent’s response on AgentCore Runtime

Use Strands with an OpenAI model and a small, simulated weather tool. Each invocation gets its own client and agent. In a deployed Runtime, AgentCore Identity supplies the API key for that request. The key never enters the deploy command or a global SDK client.

The endpoint accepts `{"prompt":"What is the demo forecast for Seattle?"}`. On success it returns **server-sent events**, not a single JSON response. Each `data:` line holds a JSON string containing a text delta. Tool arguments, raw framework events and request context are not streamed. Invalid input or missing credentials fail before streaming starts; if the model fails after text has started, the final event is a JSON object with an `error` field. Don't treat partial text as a completed answer. This replaces the old calculator stream and single-message Identity example; it is not their identical wire format.

## Try without credentials

With Python 3.13 and uv, from this directory:

```bash
uv sync --frozen
uv run --frozen python -m unittest -v test_streaming
```

The tests exercise the actual weather tool and streaming SDK with controlled provider events. To try **paid** local inference, use a private Bash terminal on a trusted host:

```bash
(read -rsp 'OpenAI key: ' OPENAI_API_KEY && echo && export OPENAI_API_KEY LOCAL_DEV=1 &&
 uv run --frozen uvicorn strands_openai_identity:app --host 127.0.0.1 --port 8080)
```

From a second terminal, use `curl -N` to avoid buffering the output:

```bash
curl -N --fail-with-body http://127.0.0.1:8080/invocations -H 'Content-Type: application/json' \
  -d '{"prompt":"Use the weather tool for Seattle"}'
```

Stop the server with Ctrl-C. Local HTTP is unauthenticated; don't expose or tunnel it. Use fictional prompts and do not enable debug logging of provider requests.

## Deploy, invoke and clean up

Follow the [external-model Runtime walkthrough](../EXTERNAL_MODEL_RUNTIME.md) **starting in this directory**. It uses AgentCore CLI 1.0.3, an owner-approved execution role and an externally managed OpenAI API-key provider. Do not set `LOCAL_DEV` or supply the raw key when deploying.

## Looking for file access or external tracing?

- For user-scoped file access, see [the S3 mount data isolation example](../../../01-features/02-host-your-agent/01-runtime/03-advanced/18-s3-mount-rental-agencies/). It teaches an IAM-enforced boundary rather than letting a model edit arbitrary local paths with consent bypassed. It is not a replacement for a general editor.
- For agents hosted **outside** Runtime, use the independent [observability example](observability/). It has its own dependencies and does not use the cloud deployment steps above.
