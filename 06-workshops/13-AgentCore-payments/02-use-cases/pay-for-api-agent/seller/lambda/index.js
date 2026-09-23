/**
 * Fun Facts x402 seller — Node.js AWS Lambda function.
 *
 * Mirrors the house-standard pattern used by agentcore-payments sellers
 * (see backend/lambdas/sellers/crypto-price):
 *
 *   - `@x402/hono` `paymentMiddlewareFromHTTPServer` does the full 402 +
 *     facilitator verify/settle handshake for us — no manual base64 header
 *     assembly, no manual /verify /settle HTTP calls.
 *   - Chain Agnostic Improvement Proposal 2 (CAIP-2) network identifiers
 *     (`eip155:84532` for Base Sepolia, `solana:…` for Devnet) — this is
 *     what the AgentCore Payments plugin emits on the wire when it signs
 *     an x402 payload, not the short `base-sepolia` / `solana-devnet`
 *     strings.
 *   - Price expressed as human-readable USD (`"$0.01"`) — the x402
 *     middleware converts to on-chain atomic amounts.
 *   - Response shape: `{ x402_content, x402_meta }` — the bazaar-friendly
 *     schema the AgentCore Registry can index.
 *   - `declareDiscoveryExtension` so this seller is discoverable through
 *     the Bazaar Model Context Protocol (MCP).
 *
 * Multi-network: when both `SELLER_WALLET_ADDRESS` (EVM) and
 * `SELLER_SOLANA_WALLET_ADDRESS` are set, both `accepts` entries are
 * emitted and the agent picks whichever network its instrument is on.
 */
import { Hono } from "hono";
import { handle } from "hono/aws-lambda";
import {
  paymentMiddlewareFromHTTPServer,
  x402HTTPResourceServer,
  x402ResourceServer,
} from "@x402/hono";
import { HTTPFacilitatorClient } from "@x402/core/server";
import { registerExactEvmScheme } from "@x402/evm/exact/server";
// SVM = Solana Virtual Machine — the on-chain runtime Solana programs
// execute under. The x402 SVM scheme builds + verifies SPL-token
// transfer transactions on Solana.
import { registerExactSvmScheme } from "@x402/svm/exact/server";
import {
  bazaarResourceServerExtension,
  declareDiscoveryExtension,
} from "@x402/extensions/bazaar";

// ── Config (from Lambda env vars) ───────────────────────────────────────
// Wallet addresses default to "WALLET_NOT_CONFIGURED" so an unconfigured
// seller emits clearly invalid placeholders in the 402 response. The
// facilitator rejects them at settlement and the agent surfaces a
// helpful error pointing the operator at SELLER_WALLET_ADDRESS /
// SELLER_SOLANA_WALLET_ADDRESS in `.env`.
const X402_CONFIG = {
  facilitatorUrl:
    process.env.X402_FACILITATOR_URL || "https://x402.org/facilitator",
  // CAIP-2 network identifiers
  evmNetwork: "eip155:84532", // Base Sepolia
  evmPayTo: process.env.SELLER_WALLET_ADDRESS || "WALLET_NOT_CONFIGURED",
  solanaNetwork: "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1", // Devnet
  solanaPayTo:
    process.env.SELLER_SOLANA_WALLET_ADDRESS || "WALLET_NOT_CONFIGURED",
};

const PRICE = process.env.X402_PRICE || "$0.01";

// ── Fun Facts data ──────────────────────────────────────────────────────
const FACTS = {
  space: [
    "A day on Venus is longer than its year — it takes 243 Earth days to rotate but only 225 days to orbit the sun.",
    "Neutron stars are so dense that a sugar-cube-sized sample would weigh about 1 billion tons on Earth.",
    "The largest known volcano in the solar system, Olympus Mons on Mars, is nearly three times taller than Mount Everest.",
    "There is a planet made largely of diamond — 55 Cancri e, about 40 light-years away.",
    "Saturn's density is so low that, hypothetically, it would float in a bathtub of water large enough to hold it.",
  ],
  oceans: [
    "More than 80 percent of the ocean has never been mapped, explored, or even seen by humans.",
    "The Mariana Trench reaches nearly 11,000 meters deep — taller than Mount Everest turned upside down.",
    "Hydrothermal vents on the ocean floor support ecosystems that never see sunlight.",
    "Blue whales' hearts are so large that a human could swim through their arteries.",
    "Plankton in the ocean produce more than half of the oxygen we breathe.",
  ],
  ai: [
    "The term 'artificial intelligence' was coined at the Dartmouth Workshop in 1956.",
    "Transformer architectures, introduced in 2017, underpin nearly every modern large language model.",
    "Reinforcement learning from human feedback (RLHF) is what made instruction-following LLMs practical.",
    "Chess AI definitively surpassed human world champions in 1997 with IBM's Deep Blue.",
    "Modern LLMs are trained on tokens measured in the trillions.",
  ],
  payments: [
    "The x402 protocol revives an HTTP status code — 402 Payment Required — that was reserved in RFC 7231 but never standardized.",
    "Stablecoins like USDC settle on-chain in seconds, versus days for traditional wire transfers.",
    "Micropayments were first proposed by Ted Nelson in the 1960s as part of his Project Xanadu vision.",
    "Account abstraction on Ethereum makes gasless agent payments possible via meta-transactions.",
    "The first cryptocurrency micropayment channel was demonstrated in 2013 by Meni Rosenfeld and Peter Todd.",
  ],
  default: [
    "Honey found in Egyptian tombs is still edible — honey does not spoil.",
    "Octopuses have three hearts and blue blood.",
    "Bananas are berries, but strawberries are not.",
    "The Eiffel Tower can grow more than 15 cm taller in summer due to thermal expansion.",
    "Wombat droppings are cube-shaped.",
  ],
};

const SUPPORTED_TOPICS = Object.keys(FACTS).filter((k) => k !== "default");

function pickFact(rawTopic) {
  const key = String(rawTopic || "").trim().toLowerCase();
  const resolved = FACTS[key] ? key : "default";
  const pool = FACTS[resolved];
  return { topic: resolved, fact: pool[Math.floor(Math.random() * pool.length)] };
}

function buildAccepts(price) {
  const NOT_CONFIGURED = "WALLET_NOT_CONFIGURED";
  const accepts = [];
  // Treat the placeholder the same as an unset env var: still emit the
  // accepts entry so the 402 response has the right shape, but the
  // facilitator will reject any payment proof at settlement and the
  // agent surfaces a clear error message.
  if (X402_CONFIG.evmPayTo && X402_CONFIG.evmPayTo !== NOT_CONFIGURED) {
    accepts.push({
      scheme: "exact",
      price,
      network: X402_CONFIG.evmNetwork,
      payTo: X402_CONFIG.evmPayTo,
    });
  }
  if (X402_CONFIG.solanaPayTo && X402_CONFIG.solanaPayTo !== NOT_CONFIGURED) {
    accepts.push({
      scheme: "exact",
      price,
      network: X402_CONFIG.solanaNetwork,
      payTo: X402_CONFIG.solanaPayTo,
    });
  }
  if (!accepts.length) {
    // No wallet configured — emit an EVM entry anyway so the 402 response
    // has the right shape; the facilitator will reject the proof at
    // settlement. Keeps the error message useful during first-run setup.
    accepts.push({
      scheme: "exact",
      price,
      network: X402_CONFIG.evmNetwork,
      payTo: NOT_CONFIGURED,
    });
  }
  return accepts;
}

// ── Hono app + x402 middleware ──────────────────────────────────────────
const app = new Hono();

// Request logging — same shape as the reference seller so CloudWatch
// queries are portable.
app.use("*", async (c, next) => {
  const start = Date.now();
  const sig = c.req.header("payment-signature");
  console.log(
    JSON.stringify({
      event: "request_in",
      method: c.req.method,
      path: c.req.path,
      hasPaymentSignature: !!sig,
      paymentSignatureLength: sig?.length || 0,
    })
  );
  await next();
  console.log(
    JSON.stringify({
      event: "response_out",
      method: c.req.method,
      path: c.req.path,
      status: c.res.status,
      durationMs: Date.now() - start,
      hasPaymentSignature: !!sig,
    })
  );
});

// x402 server — EVM + SVM schemes, Bazaar discovery extension.
const facilitatorClient = new HTTPFacilitatorClient({
  url: X402_CONFIG.facilitatorUrl,
});
const server = new x402ResourceServer(facilitatorClient);
registerExactEvmScheme(server);
registerExactSvmScheme(server);
server.registerExtension(bazaarResourceServerExtension);

// Declare one paid route: GET /facts. The Bazaar discovery extension
// exposes the topic query-parameter schema + an example output so the
// AgentCore Registry can list this seller.
const routes = {
  "GET /facts": {
    accepts: buildAccepts(PRICE),
    extensions: {
      ...declareDiscoveryExtension({
        input: { topic: "space" },
        inputSchema: {
          properties: {
            topic: {
              type: "string",
              description: `One of ${SUPPORTED_TOPICS.join(", ")} (or any other string for a random general fact).`,
            },
          },
          required: [],
        },
        bodyType: "query",
        output: {
          example: {
            x402_content: {
              type: "text",
              data: '{"topic":"space","fact":"A day on Venus is longer than its year …"}',
              title: "Fun fact: space",
              mime_type: "application/json",
            },
            x402_meta: {
              seller: "pay-for-api-fun-facts",
              version: "1.0",
            },
          },
        },
      }),
    },
  },
};

const httpServer = new x402HTTPResourceServer(server, routes);
await httpServer.initialize();
app.use(
  paymentMiddlewareFromHTTPServer(httpServer, undefined, undefined, false)
);

// ── Routes ──────────────────────────────────────────────────────────────

// Paid route
app.get("/facts", (c) => {
  const topic = c.req.query("topic") || "default";
  const { topic: resolvedTopic, fact } = pickFact(topic);
  return c.json({
    x402_content: {
      type: "text",
      data: JSON.stringify({ topic: resolvedTopic, fact }),
      title: `Fun fact: ${resolvedTopic}`,
      mime_type: "application/json",
    },
    x402_meta: {
      seller: "pay-for-api-fun-facts",
      version: "1.0",
      generated_at: new Date().toISOString(),
      supported_topics: SUPPORTED_TOPICS,
    },
  });
});

// Public health check — no payment required.
app.get("/health", (c) =>
  c.json({
    status: "ok",
    service: "pay-for-api-fun-facts",
    price: PRICE,
    networks: buildAccepts(PRICE).map((a) => a.network),
    supported_topics: SUPPORTED_TOPICS,
  })
);

// Discovery root.
app.get("/", (c) =>
  c.json({
    service: "pay-for-api-fun-facts",
    paidEndpoints: ["GET /facts?topic=<topic>"],
    price: PRICE,
  })
);

export const handler = handle(app);
