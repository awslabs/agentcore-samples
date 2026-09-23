/**
 * Demo x402 content provider — AgentCore Payments "Pay for Content" use case.
 *
 * Serves paywalled content using the x402 v2 protocol. The page loads at HTTP 200
 * with content locked behind a visible UI widget. The x402 payment requirement is
 * embedded in a <script id="x402-requirement"> element so the browser agent can
 * read it without parsing HTTP headers.
 *
 * Usage:
 *   npm install
 *   PAY_TO=0x<your-wallet-address> npm start
 *
 * Environment variables:
 *   PORT              Server port (default: 3000)
 *   PAY_TO            Merchant wallet address to receive USDC (required)
 *   PRICE_USDC_UNITS  Payment amount in USDC atomic units, 6 decimals
 *                     (default: 1000 = $0.001 USDC)
 *   NETWORK           CAIP-2 network identifier (default: eip155:84532 = Base Sepolia)
 *   USDC_ADDRESS      USDC contract address (default: Base Sepolia USDC)
 */

const express = require("express");

const PORT = parseInt(process.env.PORT || "3000", 10);
const PAY_TO = process.env.PAY_TO;
const PRICE_USDC_UNITS = process.env.PRICE_USDC_UNITS || "1000";
const NETWORK = process.env.NETWORK || "eip155:84532";
// USDC contract on Base Sepolia testnet
const USDC_ADDRESS =
  process.env.USDC_ADDRESS || "0x036CbD53842c5426634e7929541eC2318f3dCF7e";

if (!PAY_TO) {
  console.error("ERROR: PAY_TO environment variable is required.");
  console.error("  Set it to your merchant wallet address: PAY_TO=0x... npm start");
  process.exit(1);
}

const priceUsdc = (parseInt(PRICE_USDC_UNITS, 10) / 1_000_000).toFixed(6);

const app = express();

// ── Paywall page — serves at HTTP 200, content locked until payment proof ──
app.get("/article/paywall-demo", (req, res) => {
  const requirement = {
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
        resource: `http://localhost:${PORT}/article/paywall-demo`,
        description: `Premium article — ${priceUsdc} USDC`,
      },
    ],
  };

  const requirementJson = JSON.stringify(requirement, null, 2);

  res.setHeader("Content-Type", "text/html");
  res.send(`<!DOCTYPE html>
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
  <!-- x402 payment requirement — read by the browser agent from the DOM -->
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

  <!-- Paywall UI widget — browser agent discovers and interacts with these elements -->
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

      // Proof passes client-side validation — unlock content
      // In a production deployment this would POST to a backend endpoint
      // that calls the x402 facilitator to verify on-chain settlement.
      // For this demo, client-side validation is sufficient to demonstrate the flow.
      document.getElementById('paywall-widget').style.display = 'none';
      document.getElementById('content-section').style.display = 'block';
      document.querySelector('.blur-preview').style.display = 'none';
    }
  </script>
</body>
</html>`);
});

// ── Health check ────────────────────────────────────────────────────────────
app.get("/health", (req, res) => {
  res.json({ status: "ok", network: NETWORK, payTo: PAY_TO });
});

app.listen(PORT, () => {
  console.log(`\n✅ Content provider running at http://localhost:${PORT}`);
  console.log(`   Paywall demo:  http://localhost:${PORT}/article/paywall-demo`);
  console.log(`   Health check:  http://localhost:${PORT}/health`);
  console.log(`   Price:         ${priceUsdc} USDC (${PRICE_USDC_UNITS} atomic units)`);
  console.log(`   Network:       ${NETWORK}`);
  console.log(`   Pay-to:        ${PAY_TO}\n`);
});
