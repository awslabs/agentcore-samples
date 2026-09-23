#!/usr/bin/env node
/**
 * mcp-gateway-proxy — stdio-to-StreamableHTTP bridge with SigV4 signing.
 *
 * Claude Code spawns this as a stdio MCP server. It:
 *  1. Reads JSON-RPC messages from stdin (StdioServerTransport)
 *  2. Forwards them to the AgentCore Gateway via StreamableHTTPClientTransport
 *  3. SigV4-signs every outbound request using the runtime's IAM identity
 *  4. Writes responses back to stdout
 *
 * Usage (.mcp.json):
 *   {
 *     "mcpServers": {
 *       "gateway": {
 *         "type": "stdio",
 *         "command": "node",
 *         "args": ["/app/mcp-gateway-proxy/index.js", "--gateway-url", "https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp"]
 *       }
 *     }
 *   }
 */

import { parseArgs } from "node:util";
import crypto from "node:crypto";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { SignatureV4 } from "@smithy/signature-v4";
import { Hash } from "@smithy/hash-node";
import { HttpRequest } from "@smithy/protocol-http";
import { fromNodeProviderChain } from "@aws-sdk/credential-providers";

const { values } = parseArgs({
	args: process.argv.slice(2),
	options: {
		"gateway-url": { type: "string" },
		region: { type: "string", default: process.env.AWS_REGION || "us-west-2" },
	},
});

const gatewayUrl = values["gateway-url"];
const region = values["region"];

if (!gatewayUrl) {
	console.error(
		"Usage: mcp-gateway-proxy --gateway-url <url> [--region <region>]",
	);
	process.exit(1);
}

const SERVICE = "bedrock-agentcore";

const signer = new SignatureV4({
	credentials: fromNodeProviderChain(),
	region,
	service: SERVICE,
	sha256: Hash.bind(null, "sha256"),
});

function createSigV4Fetch(innerFetch) {
	return async (input, init) => {
		const url =
			input instanceof URL
				? input
				: input instanceof Request
					? new URL(input.url)
					: new URL(input.toString());

		const method =
			init?.method ?? (input instanceof Request ? input.method : "GET");

		let body;
		if (init?.body) {
			body =
				typeof init.body === "string"
					? init.body
					: Buffer.from(init.body).toString();
		} else if (input instanceof Request && input.body) {
			body = await input.text();
		}

		// Downgrade protocol version in initialize request body
		if (body) {
			try {
				const parsed = JSON.parse(body);
				if (parsed.method === "initialize" && parsed.params?.protocolVersion) {
					parsed.params.protocolVersion = "2025-03-26";
					body = JSON.stringify(parsed);
				}
			} catch {}
		}

		const headers = { host: url.hostname };
		const sourceHeaders =
			init?.headers ?? (input instanceof Request ? input.headers : undefined);

		if (sourceHeaders) {
			const entries =
				sourceHeaders instanceof Headers
					? sourceHeaders.entries()
					: Array.isArray(sourceHeaders)
						? sourceHeaders.values()
						: Object.entries(sourceHeaders).values();

			const hopByHop = new Set([
				"connection",
				"keep-alive",
				"transfer-encoding",
				"upgrade",
			]);
			for (const [key, value] of entries) {
				if (!hopByHop.has(key.toLowerCase())) {
					headers[key.toLowerCase()] = value;
				}
			}
		}

		// Override protocol version to match gateway requirement
		if (headers["mcp-protocol-version"]) {
			headers["mcp-protocol-version"] = "2025-03-26";
		}

		const httpRequest = new HttpRequest({
			method: method.toUpperCase(),
			hostname: url.hostname,
			port: url.port ? Number(url.port) : undefined,
			path: url.pathname,
			query: Object.fromEntries(url.searchParams.entries()),
			headers,
			body,
		});

		const signed = await signer.sign(httpRequest);

		// Always set protocol version to 2025-03-26 (gateway requirement)
		const finalHeaders = { ...signed.headers };
		finalHeaders["mcp-protocol-version"] = "2025-03-26";

		return innerFetch(input, {
			...init,
			method: signed.method,
			headers: finalHeaders,
			body: signed.body,
		});
	};
}

const log = (msg) => process.stderr.write(`[mcp-gateway-proxy] ${msg}\n`);

async function main() {
	log(`starting — gateway=${gatewayUrl} region=${region}`);

	const sigv4Fetch = createSigV4Fetch(fetch);

	const serverTransport = new StreamableHTTPClientTransport(
		new URL(gatewayUrl),
		{ fetch: sigv4Fetch },
	);

	const clientTransport = new StdioServerTransport();

	clientTransport.onmessage = (message) => {
		serverTransport.send(message).catch((err) => {
			log(`error sending to gateway: ${err.message}`);
			if (message.id !== undefined) {
				clientTransport
					.send({
						jsonrpc: "2.0",
						id: message.id,
						error: { code: -32603, message: err.message },
					})
					.catch(() => {});
			}
		});
	};

	serverTransport.onmessage = (message) => {
		clientTransport.send(message).catch((err) => {
			log(`error sending to client: ${err.message}`);
		});
	};

	clientTransport.onclose = () => {
		log("stdin closed, shutting down");
		serverTransport.close().catch(() => {});
		process.exit(0);
	};

	serverTransport.onclose = () => {
		log("gateway connection closed");
	};

	serverTransport.onerror = (err) => {
		log(`gateway error: ${err.message}`);
	};

	clientTransport.onerror = (err) => {
		log(`stdio error: ${err.message}`);
	};

	await serverTransport.start();
	await clientTransport.start();

	log("proxy ready");
}

main().catch((err) => {
	log(`fatal: ${err.message}`);
	process.exit(1);
});