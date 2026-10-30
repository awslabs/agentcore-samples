# Bidirectional Streaming

## Overview

AgentCore runtime supports persistent WebSocket connections for real-time bidirectional streaming. This enables interactive applications like voice agents, collaborative editing, and live data processing where both client and server need to send data continuously.

## Samples in this directory

Five complete, self-contained voice agents. Each includes server code, a Dockerfile, a browser client, and a deploy script.

| Sample | Architecture | Framework | Key Feature |
|:-------|:-------------|:----------|:------------|
| **01-bedrock-sonic-ws** | Native Speech-to-Speech | Raw Bedrock SDK | Full protocol control over Nova Sonic |
| **02-strands-ws** | Native S2S (multi-model) | Strands BidiAgent | MCP Gateways, Nova Sonic / Gemini / OpenAI |
| **03-langchain-transcribe-polly-ws** | STT to LLM to TTS | LangChain + Transcribe + Polly | Text LLM with voice I/O pipeline |
| **04-pipecat-sonic-ws** | Native S2S | Pipecat pipeline | Open-source framework, RTVI/Protobuf |
| **05-bidirectional-streaming-webrtc** | WebRTC to Native S2S | aiortc + KVS | Browser WebRTC instead of WebSockets |

Start with the README in the sample you want to run. Each one is runnable on its own.

## What these samples involve

Bidirectional streaming agents are more involved than the request/response examples elsewhere in this section. They require:

- **WebSocket or WebRTC server implementations** rather than simple HTTP request/response
- **Docker containers**, since streaming servers cannot use the zip-to-S3 code deployment path
- **Browser-based clients** in HTML/JS for audio capture and playback
- **Additional AWS services** such as Amazon Transcribe, Amazon Polly, and Kinesis Video Streams

Samples `01` through `04` share the deploy tooling in `utils/`. Sample `05` has its own `deploy.py`, because WebRTC needs VPC network mode for outbound UDP to reach the KVS TURN servers.

## Running on AgentCore Runtime V2

All five samples deploy to **AgentCore Runtime V2**, selected through the `platformVersion` field.

On a cold start, an execution environment has to load your agent's code and dependencies before it can serve requests -- pulling the container image, or preparing a direct code deployment -- and how long that takes varies from one start to the next. V2 prepares the environment once when you create or update the runtime and snapshots it, so new environments resume from that snapshot instead of loading your code again. Startup is faster, and because every environment starts from the same snapshot, more consistent. Consistency is the part that matters for a voice agent, where a slow start is immediately audible to whoever is talking.

Creating or updating a runtime includes preparing that snapshot, so expect it to take longer. The deploy scripts poll and print status, so a `CREATING` phase is expected rather than a hang.

One thing to watch for in your own agents: **anything captured at startup is frozen into the snapshot.** Whatever the container does while being snapshotted is restored with it, possibly hours later. That is why these samples resolve AWS credentials per session rather than at startup, which would otherwise return `403 ExpiredTokenException`, and why sample `05` builds its audio resampler per session rather than at import, which would otherwise produce silent audio. The rule: do not cache credentials, timers, or native library handles at import or during startup.

Deployment requires `boto3>=1.43.95`. Neither `CreateAgentRuntime` nor `UpdateAgentRuntime` echoes `platformVersion` back, so the scripts confirm it with `GetAgentRuntime`. V2 is not available in every region, so check availability for the region you deploy into.

## Architecture Patterns

### Native Speech-to-Speech (S2S)
Audio flows directly into a model that understands speech and responds with speech (Nova Sonic, Gemini, OpenAI Realtime). Lower latency, simpler pipeline, built-in VAD and barge-in.

### Sandwich (STT to LLM to TTS)
Audio is transcribed to text (Amazon Transcribe), processed by a text LLM (any model), then synthesized back to speech (Amazon Polly). More flexible, since any text LLM works, but higher latency.

## Key AgentCore Features for Streaming

- **WebSocket proxy with SigV4 authentication** so clients connect through AgentCore's authenticated endpoint
- **Container deployment via ECR** to package your streaming server as a Docker container
- **IAM role management** so AgentCore provisions execution roles with model access
- **Auto-scaling and lifecycle management** handled by AgentCore
