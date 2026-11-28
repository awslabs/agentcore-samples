# Google ADK search grounding on AgentCore Runtime

Ask a Gemini agent a question and get back final answer text. Google ADK registers its built-in Google Search tool; Gemini decides whether to use it. Pass `"include_grounding": true` for an object with `answer` and provider-supplied `grounding` metadata (or `null` if absent). For a user-facing UI, retain citations and follow [Google’s Search grounding display requirements](https://ai.google.dev/gemini-api/docs/google-search). The sample itself doesn’t render third-party HTML.

The default model is [`gemini-3.5-flash-lite`](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite). Check availability and quotas in your Google project; if needed set `GEMINI_MODEL_ID` to another model supporting Search. Each invocation creates its own ADK runner, model client and in-memory session. Reusing a Runtime session ID does **not** persist chat history. The optional payload field `user_id` is an ADK label, not an authorization decision.

## Try it locally

With Python 3.13 and uv, from this directory:

```bash
uv sync --frozen
uv run --frozen python -m unittest discover -s tests -v
```

The tests exercise the real ADK runner and Runtime HTTP adapter using controlled provider responses. No account or key is needed. To try **paid** local inference, use a private Bash terminal on a trusted machine:

```bash
(read -rsp 'Gemini key: ' GEMINI_API_KEY && echo && export GEMINI_API_KEY LOCAL_DEV=1 &&
 uv run --frozen uvicorn adk_agent_google_search:app --host 127.0.0.1 --port 8080)
```

From a second terminal:

```bash
curl --fail-with-body http://127.0.0.1:8080/invocations -H 'Content-Type: application/json' \
  -d '{"prompt":"What was announced at Google I/O?","include_grounding":true}'
```

Stop the server with Ctrl-C. Local HTTP is unauthenticated; don't expose or tunnel it. Do not enable request-body or provider debug logging.

## Deploy

Follow [Deploy the external-model Runtime](../EXTERNAL_MODEL_RUNTIME.md) **starting in this directory**. It uses AgentCore CLI 1.0.3, an explicitly authorized execution role and an externally managed AgentCore Identity API-key provider. Do not set `LOCAL_DEV` or supply a raw model key during deployment. For cloud invocation and cleanup, use the commands in that guide.
