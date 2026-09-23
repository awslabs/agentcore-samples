# Use Cases

Real-world use cases that demonstrate **Amazon Bedrock AgentCore payments** in action. Each use case is a standalone sample with its own notebook, environment configuration, and supporting infrastructure.

## Available use cases

### [Pay for Content (Browser Use)](pay-for-content-browser-use/)

An AI agent built with **Strands Agents** and **AgentCoreBrowser** autonomously navigates a paywalled website, reads the x402 payment requirement from the page DOM, processes a payment via AgentCore payments, and returns the unlocked content. No private keys held by the agent, no human involvement in the payment step.

**Highlights**
- Browser-based x402 flow (DOM-embedded payment requirement, not HTTP 402 interception)
- IAM role separation between session management and payment execution
- Embedded wallet provisioning via Coinbase CDP
- Deployable CDK content-provider stack included for end-to-end testing
- Tested end-to-end on Base Sepolia testnet

---

### [Pay for Data (Heurist)](pay-for-data/)

A finance research agent that calls paid **Heurist x402 endpoints** for live market prices, SEC filings, and macro indicators, analyzes the data with **AgentCore Code Interpreter**, and returns charts and reports as S3 presigned URLs. The `AgentCorePaymentsPlugin` handles the entire x402 payment lifecycle — tool code stays a plain `http_request` call.

**Highlights**
- HTTP 402 interception and automatic payment retry via AgentCorePaymentsPlugin
- Parallel paid tool calls with USDC settlement on Base mainnet
- AgentCore Code Interpreter for pandas/matplotlib analysis and S3 artifact export
- Deployed to AgentCore Runtime with full AgentCore observability

> ⚠️ **Mainnet sample.** This use case settles real USDC on Base mainnet. Fund your embedded wallet before running. Typical per-call prices are $0.002–$0.005; $1 USDC covers ~200 calls.

---

### [Pay for API](pay-for-api-agent/)

An AI agent built with **Strands Agents** autonomously pays for metered access to an HTTP API through AgentCore payments. The seller is a "Fun Facts" Amazon API Gateway and AWS Lambda service deployed via AWS CDK that charges $0.01 per call and accepts payment on either EVM or Solana. When the agent hits HTTP 402, the `AgentCorePaymentsPlugin` forwards the requirement to AgentCore payments, attaches the signed proof, and retries. The agent's tool code stays a plain `http_request` call.

**Highlights**
- HTTP 402 interception via `AgentCorePaymentsPlugin` (no browser, no manual handshake)
- Multi-provider: the same agent code runs against Coinbase CDP and Stripe via Privy
- Multi-network: EVM (Base Sepolia) and Solana (Solana Devnet) on testnets
- Four IAM roles enforce separation of duties between control plane, management, payment signing, and credential retrieval
- Self-contained: the notebook provisions the AgentCore payments stack inline and deploys the seller from an included CDK app
- AgentCore Runtime deploy with CloudWatch Transaction Search and the GenAI Observability dashboard
