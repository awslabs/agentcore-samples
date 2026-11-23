# Strands Voice Agent (Multi-Model Speech-to-Speech)

A bidirectional voice agent supporting three S2S models: **Amazon Nova Sonic**, **Google Gemini 2.5 Flash Native Audio**, and **OpenAI GPT Realtime**. Built with Strands `BidiAgent`, deployed on Amazon Bedrock AgentCore for incremental audio and text events.

## Deploy to AgentCore

Follow the [shared WebSocket deployment and cleanup guide](../WEBSOCKET_DEPLOYMENT.md) with `SAMPLE=02-strands-ws`. It creates one Runtime V2 from a prepared image and role.

## Local testing

Install the `websocket/requirements.txt` in a dedicated Python 3.12 environment, then start `python websocket/server.py` from this sample directory. In another environment install `client/requirements.txt` and run `python client/client.py --ws-url ws://localhost:8080/ws`. Use a short-lived AWS profile for Nova Sonic. Gemini and OpenAI use their separate provider credentials for inference only; never bake them into the Runtime or image.

## What the voice agent does

The browser sends an initial configuration and streams microphone audio over the WebSocket. The server creates one Strands `BidiAgent` per connection, selects a Nova Sonic, Gemini Live or OpenAI Realtime model, and returns incremental events. Large audio events are divided on base64 boundaries for the Runtime WebSocket frame limit. Try a short conversation using made-up details.

This voice demo does **not** authenticate banking customers, query accounts, retrieve knowledge-base answers or transfer funds. It runs with an empty tool list. Gateway ARNs passed to the old model constructors did not register tools, and the application never consumed the provisioned Memory. The separate `mcp/` scripts are not deployed by this workflow. For a Gateway tutorial start with the [Gateway samples](../../../../../07-centralize-and-govern-your-ai-infrastructure/01-gateway/).
