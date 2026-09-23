# Demo Content Provider — x402 Paywall

A CloudFront + Lambda@Edge deployment that serves paywalled content using the x402 v2
protocol. Used as the "seller" side of the **Pay for Content (Browser)** use case.

> **Note:** This content provider is a demo and is intended only as a test target for the
> sample notebook. It is not a reference implementation for x402 paywall verification.

## How it works

- The paywall page loads at HTTP 200 — content is visible in the DOM but locked behind a UI widget
- The x402 payment requirement is embedded in `<script id="x402-requirement">` so the browser agent
  can read it without parsing HTTP headers
- Lambda@Edge generates the paywall page dynamically with the correct wallet address and price
- After a valid base64-encoded proof is submitted, the content unlocks client-side

## Prerequisites

- AWS CLI v2 configured (`aws configure`)
- Node.js 18+
- AWS CDK v2 (`npm install -g aws-cdk`)
- A merchant wallet address to receive USDC payments (`0x...`)

## Deploy

```bash
cd content-provider
PAY_TO=0x<your-wallet-address> bash deploy.sh
```

`deploy.sh` will:
1. Install CDK dependencies (`npm install` in `cdk/`)
2. Bootstrap CDK in `us-east-1` (Lambda@Edge requires us-east-1; safe to re-run)
3. Deploy the CloudFront distribution + Lambda@Edge stack (~5 minutes first time)
4. Print the CloudFront URL at the end

Copy the printed URL into your `.env` file:

```
CONTENT_DISTRIBUTION_URL=https://d<id>.cloudfront.net
```

## Optional configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PAY_TO` | **required** | Merchant wallet address to receive USDC |
| `PRICE_USDC_UNITS` | `100000` | Price in USDC atomic units (6 decimals); `100000` = $0.10 USDC |
| `NETWORK` | `eip155:84532` | CAIP-2 network (Base Sepolia testnet) |
| `USDC_ADDRESS` | Base Sepolia USDC | USDC contract address |
| `AWS_PROFILE` | *(default profile)* | Named AWS CLI profile |

Example with overrides:

```bash
PAY_TO=0xabc... PRICE_USDC_UNITS=100000 bash deploy.sh
```

## Each user deploys their own stack

Each user of this use case deploys their own CDK stack. The deploy output prints the
CloudFront URL — copy it into `CONTENT_DISTRIBUTION_URL` in your `.env` file.

```
CONTENT_DISTRIBUTION_URL=https://d<id>.cloudfront.net
```

The CDK stack is cheap to run (CloudFront + Lambda@Edge + S3) and takes ~5 minutes to
deploy. Tear it down with `npx cdk destroy` when you are done.

## Running locally (for development only)

The Express.js server (`index.js`) can be used for local development:

```bash
npm install
PAY_TO=0x<your-wallet-address> npm start
```

The server starts at `http://localhost:3000`.

**Important:** `AgentCoreBrowser` is a cloud-managed browser and cannot reach `localhost`.
Local mode is for inspecting the page and debugging the paywall HTML only — use the CDK
deployment for any run of the actual notebook agent.

## Cleanup

From the `content-provider/` directory:

```bash
cd cdk
npx cdk destroy
```

This removes the CloudFront distribution, Lambda@Edge function, and S3 bucket.
CloudFront distributions take ~5 minutes to fully deactivate after deletion.

## DOM elements used by the browser agent

The browser agent discovers these elements dynamically. The IDs are stable for this
demo content provider; if you deploy a custom stack, inspect the page source.

| Element ID | Purpose |
|------------|---------|
| `x402-requirement` | `<script>` containing the JSON payment requirement |
| `pay-btn` | Button to initiate payment flow |
| `proof-input` | `<textarea>` for the base64-encoded proof |
| `verify-btn` | Button to submit the proof |
| `content` | `<div>` containing the unlocked article text |

**These element IDs are specific to this demo content provider.** Real x402
sites will use different selectors. The agent system prompt instructs the model to
discover payment elements dynamically using semantic cues (button text, input types,
aria-labels) rather than hardcoded IDs.

## Architecture

```
User / Browser Agent
        │ HTTPS
        ▼
┌─────────────────────────────────┐
│  CloudFront Distribution        │
│                                 │
│  /article/paywall-demo ─────────┼──► Lambda@Edge (viewer-request)
│  (Lambda@Edge behavior)         │    • Generates paywall HTML with
│                                 │      x402 requirement embedded in DOM
│  /* ────────────────────────────┼──► S3 Origin (static assets)
│  (default S3 behavior)          │    • index.html, static files
└─────────────────────────────────┘
```

Lambda@Edge injects the merchant wallet address, price, and network at CDK build time
via esbuild `--define` flags. No environment variables are needed at runtime.

