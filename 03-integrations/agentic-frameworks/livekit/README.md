# Historical LiveKit interview demo

The hosted AI video-interview example is retired. To learn how to stream audio on AgentCore without deploying an interview or candidate-data system, start with one of these maintained paths:

- [Bidirectional voice examples](../../../01-features/02-host-your-agent/01-runtime/03-advanced/03-bidirectional-streaming/): compare raw Bedrock, Strands, LangChain, Pipecat and WebRTC.
- [WebRTC with KVS and Nova Sonic](../../../01-features/02-host-your-agent/01-runtime/03-advanced/03-bidirectional-streaming/05-bidirectional-streaming-webrtc/): browser signaling, microphone audio and a VPC Runtime using TURN. It does not use LiveKit.
- [AgentCore evaluations](../../../01-features/06-observe-evaluate-optimize-your-agent/02-evaluate/): learn evaluation mechanics independently of hiring decisions or candidate records.

The [historical source](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/03-integrations/agentic-frameworks/livekit) integrated LiveKit interview rooms, an EC2-hosted UI, transcript storage and generated candidate feedback. None of the links above reproduces that workflow or is a replacement for a production interview system. Room-scoped authorization, consent, retention and access to candidate data require an explicit design before deploying such an application.

**Existing deployments:** removing these files does not stop services, revoke access or delete data. Verify the account, Region, LiveKit project and original resource IDs before acting. Disable public access to any old token/feedback endpoints and review room grants with the owner. Reconcile EC2 and container services, AgentCore Runtime/Memory/Gateway, Lambda, Cognito credentials, ECR images, S3 transcript/feedback objects and logs. Follow the data owner's retention policy; don't bulk-delete a shared bucket, memory or role. Rotate dedicated credentials if they were embedded in an image or configuration.
