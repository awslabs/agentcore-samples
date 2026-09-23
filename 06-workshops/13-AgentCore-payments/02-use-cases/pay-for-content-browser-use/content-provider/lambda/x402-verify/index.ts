/**
 * Lambda@Edge viewer-request handler — x402 v2 paywall
 *
 * Config is injected at CDK build time via esbuild --define:
 *   __PAY_TO__            Merchant wallet address
 *   __PRICE_USDC_UNITS__  Price in USDC atomic units (6 decimals)
 *   __NETWORK__           CAIP-2 network identifier
 *   __USDC_ADDRESS__      USDC contract address
 *
 * Two flows:
 *   Browser agent flow  — request has no x-payment header
 *                         → returns paywall HTML page at HTTP 200
 *                           (agent reads <script id="x402-requirement">, calls
 *                            ProcessPayment, then pastes the base64 proof into
 *                            the paywall UI — client-side JS validates and unlocks)
 *
 *   Programmatic flow   — request has x-payment header with base64 proof
 *                         → validates proof structure and returns 200 with content
 *                           (for direct HTTP clients, not the browser agent path)
 */

// Values injected by CDK esbuild --define flags
declare const __PAY_TO__: string;
declare const __PRICE_USDC_UNITS__: string;
declare const __NETWORK__: string;
declare const __USDC_ADDRESS__: string;

type CFRequest = {
  headers: Record<string, Array<{ key: string; value: string }>>;
  uri: string;
  method: string;
};

type CFEvent = {
  Records: Array<{ cf: { request: CFRequest } }>;
};

type LambdaResponse = {
  status: string;
  statusDescription: string;
  headers: Record<string, Array<{ key: string; value: string }>>;
  body: string;
};

const PAY_TO = __PAY_TO__;
const PRICE_USDC_UNITS = __PRICE_USDC_UNITS__;
const NETWORK = __NETWORK__;
const USDC_ADDRESS = __USDC_ADDRESS__;

const priceUsdc = (parseInt(PRICE_USDC_UNITS, 10) / 1_000_000).toFixed(6);

function buildRequirement(resource: string): object {
  return {
    x402Version: 2,
    accepts: [
      {
        scheme: "exact",
        network: NETWORK,
        maxAmountRequired: PRICE_USDC_UNITS,
        asset: USDC_ADDRESS,
        payTo: PAY_TO,
        maxTimeoutSeconds: 60,
        extra: { name: "USDC", version: "2" },
        resource,
        description: `Premium article — ${priceUsdc} USDC`,
      },
    ],
  };
}

function buildPaywallHtml(requirement: object): string {
  const requirementJson = JSON.stringify(requirement, null, 2);
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Premium Article — AgentCore Payments Demo</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 760px; margin: 48px auto; padding: 0 24px; color: #1a1a1a; }
    h1 { font-size: 1.8rem; margin-bottom: 8px; }
    .meta { color: #666; font-size: 0.9rem; margin-bottom: 24px; }
    .paywall-widget { border: 1px solid #e0e0e0; border-radius: 8px; padding: 24px; background: #fafafa; margin: 24px 0; }
    .paywall-widget h2 { margin: 0 0 8px; font-size: 1.1rem; }
    .paywall-widget .price { font-size: 1.4rem; font-weight: 600; color: #0057b8; margin: 12px 0; }
    .paywall-widget textarea { width: 100%; box-sizing: border-box; height: 80px; font-family: monospace; font-size: 0.75rem; margin: 12px 0; padding: 8px; border: 1px solid #ccc; border-radius: 4px; resize: vertical; }
    .paywall-widget button { padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 0.95rem; margin-right: 8px; }
    .btn-pay { background: #0057b8; color: white; }
    .btn-verify { background: #1a7a1a; color: white; display: none; }
    .status { margin-top: 12px; font-size: 0.9rem; color: #555; }
    #content-section { display: none; }
    #content-section .article-body { line-height: 1.7; }
    .blur-preview { filter: blur(4px); user-select: none; color: #888; margin: 16px 0; }
  </style>
</head>
<body>
  <!-- x402 payment requirement — read by the browser agent from the DOM.
       NOTE: The <script id="x402-requirement"> tag is a convention of this sample
       content provider. Real x402 sites may embed the requirement differently
       (e.g. HTTP headers, a <meta> tag, or a separate API endpoint). -->
  <script id="x402-requirement" type="application/json">
${requirementJson}
  </script>

  <h1>The Future of Agentic Commerce</h1>
  <div class="meta">By Demo Author · AgentCore Payments · ${priceUsdc} USDC to unlock</div>

  <p class="blur-preview">
    This premium article explores how AI agents are transforming digital commerce
    through autonomous micropayments. The content below is locked until payment
    is verified on-chain...
  </p>

  <!-- Paywall UI widget — browser agent discovers and interacts with these elements.
       NOTE: The element IDs below (pay-btn, proof-input, verify-btn, content) are
       specific to this sample content provider. Real x402 sites will use
       different selectors — the agent is instructed to discover elements dynamically
       using semantic cues (button text, input types, aria-labels). -->
  <div class="paywall-widget" id="paywall-widget">
    <h2>🔒 Premium Content</h2>
    <p>Unlock this article for <span class="price">${priceUsdc} USDC</span></p>
    <p style="font-size:0.85rem;color:#666;">
      Network: ${NETWORK} &nbsp;|&nbsp; Protocol: x402 v2
    </p>

    <!-- Step 1: agent clicks this to signal intent to pay -->
    <button class="btn-pay" id="pay-btn" onclick="showProofInput()">
      Pay ${priceUsdc} USDC to Unlock
    </button>

    <!-- Step 2: agent fills this with the base64-encoded proof envelope -->
    <textarea
      id="proof-input"
      placeholder="Paste base64-encoded x402 v2 payment proof here..."
      style="display:none"
    ></textarea>

    <!-- Step 3: agent clicks this to submit the proof -->
    <button class="btn-verify" id="verify-btn" onclick="submitProof()">
      Submit Payment Proof
    </button>

    <div class="status" id="paywall-status"></div>
  </div>

  <!-- Unlocked content — hidden until valid proof is submitted -->
  <div id="content-section">
    <h2>Full Article</h2>
    <div class="article-body" id="content">
      <p>
        Agentic commerce represents a fundamental shift in how digital transactions occur.
        Rather than requiring human approval for each micropayment, AI agents can now
        autonomously negotiate, authorize, and process payments on behalf of users —
        within strict, human-defined budget limits.
      </p>
      <p>
        Amazon Bedrock AgentCore Payments implements this vision through a layered
        architecture: a Payment Manager defines the top-level configuration, a Connector
        links it to a wallet provider (such as Coinbase CDP), an Instrument provisions
        the on-chain wallet, and a Session enforces a time-bounded spending budget.
        The agent never holds a private key — signing is delegated entirely to the
        AgentCore Payments service.
      </p>
      <p>
        The x402 protocol complements this by standardising how servers signal payment
        requirements over HTTP. A protected resource returns a 402 status (or embeds
        the requirement in the DOM for browser flows) with a machine-readable description
        of what payment is needed. The agent reads this, calls ProcessPayment to generate
        a cryptographic proof, and returns that proof in a PAYMENT-SIGNATURE header.
        The server verifies on-chain settlement before serving the content.
      </p>
      <p>
        This creates a new class of autonomous micro-commerce: agents that can access
        premium APIs, paywalled research, real-time data feeds, and AI inference services
        — paying for exactly what they use, with full auditability and human-controlled
        spending limits.
      </p>
    </div>
  </div>

  <script>
    function showProofInput() {
      document.getElementById('pay-btn').style.display = 'none';
      document.getElementById('proof-input').style.display = 'block';
      document.getElementById('verify-btn').style.display = 'inline-block';
      document.getElementById('paywall-status').textContent = 'Paste your payment proof and click Submit.';
    }

    function submitProof() {
      const proof = document.getElementById('proof-input').value.trim();
      if (!proof) {
        document.getElementById('paywall-status').textContent = 'Error: proof is empty.';
        return;
      }

      // Verify the proof is valid base64-encoded JSON with x402Version: 2
      try {
        const decoded = JSON.parse(atob(proof));
        if (decoded.x402Version !== 2) {
          document.getElementById('paywall-status').textContent =
            'Error: invalid proof — x402Version must be 2.';
          return;
        }
        if (!decoded.payload) {
          document.getElementById('paywall-status').textContent =
            'Error: invalid proof — missing payload field.';
          return;
        }
      } catch (e) {
        document.getElementById('paywall-status').textContent =
          'Error: proof is not valid base64-encoded JSON.';
        return;
      }

      // Proof passes client-side validation — unlock content.
      // This demo verifies client-side only. A real merchant would POST the proof
      // to a backend endpoint that calls the x402 facilitator to confirm on-chain
      // settlement before serving content.
      document.getElementById('paywall-widget').style.display = 'none';
      document.getElementById('content-section').style.display = 'block';
      document.querySelector('.blur-preview').style.display = 'none';
    }
  </script>
</body>
</html>`;
}

function validateProof(proofHeader: string): boolean {
  try {
    const decoded = JSON.parse(Buffer.from(proofHeader, "base64").toString());
    return (
      decoded.x402Version === 2 &&
      decoded.payload !== undefined &&
      decoded.accepted !== undefined
    );
  } catch {
    return false;
  }
}

export const handler = async (event: CFEvent): Promise<LambdaResponse | CFRequest> => {
  const request = event.Records[0].cf.request;

  // Only intercept the paywall demo route
  if (!request.uri.startsWith("/article/paywall-demo")) {
    return request;
  }

  const paymentHeader =
    request.headers["x-payment"]?.[0]?.value ??
    request.headers["payment-signature"]?.[0]?.value;

  // Programmatic client: validate proof and return content directly
  if (paymentHeader) {
    if (!validateProof(paymentHeader)) {
      return {
        status: "402",
        statusDescription: "Payment Required",
        headers: {
          "content-type": [{ key: "Content-Type", value: "application/json" }],
          "x-payment-required": [
            {
              key: "X-Payment-Required",
              value: JSON.stringify(
                buildRequirement(`https://unknown/article/paywall-demo`)
              ),
            },
          ],
        },
        body: JSON.stringify({ error: "invalid_proof", message: "x402 proof validation failed" }),
      };
    }

    // Proof is structurally valid — return unlocked content
    return {
      status: "200",
      statusDescription: "OK",
      headers: {
        "content-type": [{ key: "Content-Type", value: "application/json" }],
        "cache-control": [{ key: "Cache-Control", value: "no-store" }],
      },
      body: JSON.stringify({
        title: "The Future of Agentic Commerce",
        content:
          "Agentic commerce represents a fundamental shift in how digital transactions occur. " +
          "AI agents can now autonomously negotiate, authorize, and process payments within strict, " +
          "human-defined budget limits. Amazon Bedrock AgentCore Payments implements this through a " +
          "layered architecture: PaymentManager → PaymentConnector → PaymentInstrument → PaymentSession.",
      }),
    };
  }

  // Browser agent flow: no payment header — serve the paywall HTML page
  const host = request.headers["host"]?.[0]?.value ?? "localhost";
  const resource = `https://${host}/article/paywall-demo`;
  const requirement = buildRequirement(resource);

  return {
    status: "200",
    statusDescription: "OK",
    headers: {
      "content-type": [{ key: "Content-Type", value: "text/html; charset=utf-8" }],
      "cache-control": [{ key: "Cache-Control", value: "no-store" }],
    },
    body: buildPaywallHtml(requirement),
  };
};
