#!/usr/bin/env node

import cors from "cors";
import { parseArgs } from "node:util";
import nodeFetch, { Headers as NodeHeaders } from "node-fetch";

// Type-compatible wrappers for node-fetch to work with browser-style types
const fetch = nodeFetch;
const Headers = NodeHeaders;

import {
	StreamableHTTPClientTransport,
	StreamableHTTPError,
} from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import express from "express";
import mcpProxy from "./mcpProxy.js";
import { randomUUID, randomBytes, timingSafeEqual } from "node:crypto";

const DEFAULT_MCP_PROXY_LISTEN_PORT = "6277";

const { values } = parseArgs({
	args: process.argv.slice(2),
	options: {
		"server-url": { type: "string", default: "" },
	},
});

const is401Error = (error: unknown): boolean => {
	if (error instanceof StreamableHTTPError && error.code === 401) return true;
	if (
		error instanceof Error &&
		(error.message.includes("HTTP 401") || error.message.includes("(401)"))
	)
		return true;
	return false;
};

// Function to get HTTP headers.
const getHttpHeaders = (req: express.Request): Record<string, string> => {
	const headers: Record<string, string> = {};

	// Iterate over all headers in the request
	for (const key in req.headers) {
		const lowerKey = key.toLowerCase();

		// Check if the header is one we want to forward
		if (
			lowerKey.startsWith("mcp-") ||
			lowerKey === "authorization" ||
			lowerKey === "last-event-id"
		) {
			// Exclude the proxy's own authentication header and the Client <-> Proxy session ID header
			if (lowerKey !== "x-mcp-proxy-auth" && lowerKey !== "mcp-session-id") {
				const value = req.headers[key];

				if (typeof value === "string") {
					// If the value is a string, use it directly
					headers[key] = value;
				} else if (Array.isArray(value)) {
					// If the value is an array, use the last element
					const lastValue = value.at(-1);
					if (lastValue !== undefined) {
						headers[key] = lastValue;
					}
				}
				// If value is undefined, it's skipped, which is correct.
			}
		}
	}

	// Handle the custom auth header separately. We expect `x-custom-auth-header`
	// to be a string containing the name of the actual authentication header.
	const customAuthHeaderName = req.headers["x-custom-auth-header"];
	if (typeof customAuthHeaderName === "string") {
		const lowerCaseHeaderName = customAuthHeaderName.toLowerCase();
		const value = req.headers[lowerCaseHeaderName];

		if (typeof value === "string") {
			headers[customAuthHeaderName] = value;
		} else if (Array.isArray(value)) {
			// If the actual auth header was sent multiple times, use the last value.
			const lastValue = value.at(-1);
			if (lastValue !== undefined) {
				headers[customAuthHeaderName] = lastValue;
			}
		}
	}

	// Handle multiple custom headers (new approach)
	if (req.headers["x-custom-auth-headers"] !== undefined) {
		try {
			const customHeaderNames = JSON.parse(
				req.headers["x-custom-auth-headers"] as string,
			) as string[];
			if (Array.isArray(customHeaderNames)) {
				customHeaderNames.forEach((headerName) => {
					const lowerCaseHeaderName = headerName.toLowerCase();
					if (req.headers[lowerCaseHeaderName] !== undefined) {
						const value = req.headers[lowerCaseHeaderName];
						headers[headerName] = Array.isArray(value)
							? value[value.length - 1]
							: value;
					}
				});
			}
		} catch (error) {
			console.warn("Failed to parse x-custom-auth-headers:", error);
		}
	}
	return headers;
};

/**
 * Updates a headers object in-place, preserving the original Accept header.
 * This is necessary to ensure that transports holding a reference to the headers
 * object see the updates.
 * @param currentHeaders The headers object to update.
 * @param newHeaders The new headers to apply.
 */
const updateHeadersInPlace = (
	currentHeaders: Record<string, string>,
	newHeaders: Record<string, string>,
) => {
	// Preserve the Accept header, which is set at transport creation and
	// is not present in subsequent client requests.
	const accept = currentHeaders["Accept"];

	// Clear the old headers and apply the new ones.
	Object.keys(currentHeaders).forEach((key) => delete currentHeaders[key]);
	Object.assign(currentHeaders, newHeaders);

	// Restore the Accept header.
	if (accept) {
		currentHeaders["Accept"] = accept;
	}
};

const app = express();
app.use(cors());
app.use((req, res, next) => {
	res.header("Access-Control-Expose-Headers", "mcp-session-id");
	next();
});

const webAppTransports: Map<string, Transport> = new Map<string, Transport>(); // Web app transports by web app sessionId
const serverTransports: Map<string, Transport> = new Map<string, Transport>(); // Server Transports by web app sessionId
const sessionHeaderHolders: Map<string, { headers: HeadersInit }> = new Map(); // For dynamic header updates

// Use provided token from environment or generate a new one
const sessionToken =
	process.env.MCP_PROXY_AUTH_TOKEN || randomBytes(32).toString("hex");
const authDisabled = !!process.env.DANGEROUSLY_OMIT_AUTH;

// Origin validation middleware to prevent DNS rebinding attacks
const originValidationMiddleware = (
	req: express.Request,
	res: express.Response,
	next: express.NextFunction,
) => {
	const origin = req.headers.origin;

	// Default origins based on CLIENT_PORT or use environment variable
	const clientPort = process.env.CLIENT_PORT || "6274";
	const defaultOrigin = `http://localhost:${clientPort}`;
	const allowedOrigins = process.env.ALLOWED_ORIGINS?.split(",") || [
		defaultOrigin,
	];

	if (origin && !allowedOrigins.includes(origin)) {
		console.error(`Invalid origin: ${origin}`);
		res.status(403).json({
			error: "Forbidden - invalid origin",
			message:
				"Request blocked to prevent DNS rebinding attacks. Configure allowed origins via environment variable.",
		});
		return;
	}
	next();
};

const authMiddleware = (
	req: express.Request,
	res: express.Response,
	next: express.NextFunction,
) => {
	if (authDisabled) {
		return next();
	}

	const sendUnauthorized = () => {
		res.status(401).json({
			error: "Unauthorized",
			message:
				"Authentication required. Use the session token shown in the console when starting the server.",
		});
	};

	const authHeader = req.headers["x-mcp-proxy-auth"];
	const authHeaderValue = Array.isArray(authHeader)
		? authHeader[0]
		: authHeader;

	if (!authHeaderValue || !authHeaderValue.startsWith("Bearer ")) {
		sendUnauthorized();
		return;
	}

	const providedToken = authHeaderValue.substring(7); // Remove 'Bearer ' prefix
	const expectedToken = sessionToken;

	// Convert to buffers for timing-safe comparison
	const providedBuffer = Buffer.from(providedToken);
	const expectedBuffer = Buffer.from(expectedToken);

	// Check length first to prevent timing attacks
	if (providedBuffer.length !== expectedBuffer.length) {
		sendUnauthorized();
		return;
	}

	// Perform timing-safe comparison
	if (!timingSafeEqual(providedBuffer, expectedBuffer)) {
		sendUnauthorized();
		return;
	}

	next();
};

/**
 * Converts a Node.js ReadableStream to a web-compatible ReadableStream
 * This is necessary for the EventSource polyfill which expects web streams
 */
const createWebReadableStream = (nodeStream: any): ReadableStream => {
	let closed = false;
	return new ReadableStream({
		start(controller) {
			nodeStream.on("data", (chunk: any) => {
				if (!closed) {
					controller.enqueue(chunk);
				}
			});
			nodeStream.on("end", () => {
				if (!closed) {
					closed = true;
					controller.close();
				}
			});
			nodeStream.on("error", (err: any) => {
				if (!closed) {
					closed = true;
					controller.error(err);
				}
			});
		},
		cancel() {
			closed = true;
			nodeStream.destroy();
		},
	});
};

/**
 * Creates a `fetch` function that merges dynamic session headers with the
 * headers from the actual request, ensuring that request-specific headers like
 * `Content-Type` are preserved. For SSE requests, it also converts Node.js
 * streams to web-compatible streams.
 */
const createCustomFetch = (headerHolder: { headers: HeadersInit }) => {
	return async (
		input: RequestInfo | URL,
		init?: RequestInit,
	): Promise<Response> => {
		// Determine the headers from the original request/init.
		// The SDK may pass a Request object or a URL and an init object.
		const originalHeaders =
			input instanceof Request ? input.headers : init?.headers;

		// Start with our dynamic session headers.
		const finalHeaders = new Headers(headerHolder.headers);

		// Merge the SDK's request-specific headers, letting them overwrite.
		// This is crucial for preserving Content-Type on POST requests.
		new Headers(originalHeaders).forEach((value, key) => {
			finalHeaders.set(key, value);
		});

		// Convert Headers to a plain object for node-fetch compatibility
		const headersObject: Record<string, string> = {};
		finalHeaders.forEach((value, key) => {
			headersObject[key] = value;
		});

		// Get the response from node-fetch (cast input and init to handle type differences)
		const response = await fetch(
			input as any,
			{ ...init, headers: headersObject } as any,
		);

		// Check if this is an SSE request by looking at the Accept header
		const acceptHeader = finalHeaders.get("Accept");
		const isSSE = acceptHeader?.includes("text/event-stream");

		if (isSSE && response.body) {
			// For SSE requests, we need to convert the Node.js stream to a web ReadableStream
			// because the EventSource polyfill expects web-compatible streams
			const webStream = createWebReadableStream(response.body);

			// Create a new response with the web-compatible stream
			// Convert node-fetch headers to plain object for web Response compatibility
			const responseHeaders: Record<string, string> = {};
			response.headers.forEach((value: string, key: string) => {
				responseHeaders[key] = value;
			});

			return new Response(webStream, {
				status: response.status,
				statusText: response.statusText,
				headers: responseHeaders,
			}) as Response;
		}

		// For non-SSE requests, return the response as-is (cast to handle type differences)
		return response as unknown as Response;
	};
};

const createTransport = async (
	req: express.Request,
): Promise<{
	transport: Transport;
	headerHolder?: { headers: HeadersInit };
}> => {
	const query = req.query;
	console.log("Query parameters:", JSON.stringify(query));

	const headers = getHttpHeaders(req);
	headers["Accept"] = "text/event-stream, application/json";
	const headerHolder = { headers };

	let fetchFn = createCustomFetch(headerHolder);

	const authMode = query.authMode as string | undefined;
	if (authMode === "iam") {
		const { createSigV4Fetch } = await import("./sigv4Fetch.js");
		const targetUrl = query.url as string;
		const urlRegionMatch = targetUrl?.match(
			/\.(?:bedrock-agentcore|agentcore)\.([a-z0-9-]+)\.amazonaws\.com/,
		);
		const region =
			(query.region as string) ||
			(urlRegionMatch ? urlRegionMatch[1] : null) ||
			process.env.AWS_REGION ||
			process.env.AWS_DEFAULT_REGION ||
			"us-east-1";
		fetchFn = createSigV4Fetch(region, "bedrock-agentcore", fetchFn);
	}

	const transport = new StreamableHTTPClientTransport(
		new URL(query.url as string),
		{
			fetch: fetchFn,
		},
	);
	await transport.start();
	return { transport, headerHolder };
};

app.get(
	"/mcp",
	originValidationMiddleware,
	authMiddleware,
	async (req, res) => {
		const sessionId = req.headers["mcp-session-id"] as string;
		console.log(`Received GET message for sessionId ${sessionId}`);

		const headerHolder = sessionHeaderHolders.get(sessionId);
		if (headerHolder) {
			updateHeadersInPlace(
				headerHolder.headers as Record<string, string>,
				getHttpHeaders(req),
			);
		}

		try {
			const transport = webAppTransports.get(
				sessionId,
			) as StreamableHTTPServerTransport;
			if (!transport) {
				res.status(404).end("Session not found");
				return;
			} else {
				await transport.handleRequest(req, res);
			}
		} catch (error) {
			console.error("Error in /mcp route:", error);
			res.status(500).json({ error: "Internal server error" });
		}
	},
);

app.post(
	"/mcp",
	originValidationMiddleware,
	authMiddleware,
	async (req, res) => {
		const sessionId = req.headers["mcp-session-id"] as string | undefined;

		if (sessionId) {
			console.log(`Received POST message for sessionId ${sessionId}`);
			const headerHolder = sessionHeaderHolders.get(sessionId);
			if (headerHolder) {
				updateHeadersInPlace(
					headerHolder.headers as Record<string, string>,
					getHttpHeaders(req),
				);
			}

			try {
				const transport = webAppTransports.get(
					sessionId,
				) as StreamableHTTPServerTransport;
				if (!transport) {
					res.status(404).end("Transport not found for sessionId " + sessionId);
				} else {
					await (transport as StreamableHTTPServerTransport).handleRequest(
						req,
						res,
					);
				}
			} catch (error) {
				console.error("Error in /mcp route:", error);
				res.status(500).json({ error: "Internal server error" });
			}
		} else {
			console.log("New StreamableHttp connection request");
			try {
				const { transport: serverTransport, headerHolder } =
					await createTransport(req);

				const webAppTransport = new StreamableHTTPServerTransport({
					sessionIdGenerator: randomUUID,
					onsessioninitialized: (sessionId) => {
						webAppTransports.set(sessionId, webAppTransport);
						serverTransports.set(sessionId, serverTransport!); // eslint-disable-line @typescript-eslint/no-non-null-assertion
						if (headerHolder) {
							sessionHeaderHolders.set(sessionId, headerHolder);
						}
						console.log("Client <-> Proxy  sessionId: " + sessionId);
					},
					onsessionclosed: (sessionId) => {
						webAppTransports.delete(sessionId);
						serverTransports.delete(sessionId);
						sessionHeaderHolders.delete(sessionId);
					},
				});
				console.log("Created StreamableHttp client transport");

				await webAppTransport.start();

				mcpProxy({
					transportToClient: webAppTransport,
					transportToServer: serverTransport,
				});

				await (webAppTransport as StreamableHTTPServerTransport).handleRequest(
					req,
					res,
					req.body,
				);
			} catch (error) {
				if (is401Error(error)) {
					console.error(
						"Received 401 Unauthorized from MCP server:",
						error instanceof Error ? error.message : error,
					);
					res.status(401).json(error);
					return;
				}
				console.error("Error in /mcp POST route:", error);
				res.status(500).json({ error: "Internal server error" });
			}
		}
	},
);

app.delete(
	"/mcp",
	originValidationMiddleware,
	authMiddleware,
	async (req, res) => {
		const sessionId = req.headers["mcp-session-id"] as string | undefined;
		console.log(`Received DELETE message for sessionId ${sessionId}`);
		if (sessionId) {
			try {
				const serverTransport = serverTransports.get(
					sessionId,
				) as StreamableHTTPClientTransport;
				if (!serverTransport) {
					res.status(404).end("Transport not found for sessionId " + sessionId);
				} else {
					await serverTransport.terminateSession();
					await serverTransport.close();
					webAppTransports.delete(sessionId);
					serverTransports.delete(sessionId);
					sessionHeaderHolders.delete(sessionId);
					console.log(`Transports removed for sessionId ${sessionId}`);
				}
				res.status(200).end();
			} catch (error) {
				console.error("Error in /mcp route:", error);
				res.status(500).json({ error: "Internal server error" });
			}
		}
	},
);

// Session binding endpoint for AgentCore Identity 3LO flow
// Receives session_id and user bearer token, calls CompleteResourceTokenAuth
app.post(
	"/complete-token-auth",
	originValidationMiddleware,
	express.json(),
	async (req, res) => {
		const { sessionUri, userToken } = req.body;

		if (!sessionUri || !userToken) {
			res.status(400).json({ error: "Missing sessionUri or userToken" });
			return;
		}

		try {
			const { BedrockAgentCoreClient, CompleteResourceTokenAuthCommand } =
				await import("@aws-sdk/client-bedrock-agentcore");

			const client = new BedrockAgentCoreClient({});
			const command = new CompleteResourceTokenAuthCommand({
				sessionUri,
				userIdentifier: { userToken },
			});

			await client.send(command);
			console.log(
				"CompleteResourceTokenAuth succeeded for session:",
				sessionUri,
			);
			res
				.status(200)
				.json({ message: "OAuth2 3LO flow completed successfully" });
		} catch (error) {
			console.error("CompleteResourceTokenAuth error:", error);
			res.status(500).json({ error: String(error) });
		}
	},
);

app.get("/health", (req, res) => {
	res.json({
		status: "ok",
	});
});

app.get("/config", originValidationMiddleware, authMiddleware, (req, res) => {
	try {
		res.json({
			defaultTransport: "streamable-http",
			defaultServerUrl: values["server-url"],
			awsRegion:
				process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || "us-east-1",
		});
	} catch (error) {
		console.error("Error in /config route:", error);
		res.status(500).json({ error: "Internal server error" });
	}
});

app.get("/gateways", originValidationMiddleware, async (req, res) => {
	try {
		const {
			BedrockAgentCoreControlClient,
			ListGatewaysCommand,
			GetGatewayCommand,
		} = await import("@aws-sdk/client-bedrock-agentcore-control");

		const queryRegion = req.query.region as string | undefined;

		const client = queryRegion
			? new BedrockAgentCoreControlClient({ region: queryRegion })
			: new BedrockAgentCoreControlClient({});

		const region = queryRegion || (await client.config.region()) || "us-east-1";

		const gatewayId = req.query.gatewayId as string | undefined;

		if (gatewayId) {
			try {
				const detail = await client.send(
					new GetGatewayCommand({ gatewayIdentifier: gatewayId }),
				);
				res.json({
					gatewayUrl: detail.gatewayUrl ?? "",
					gatewayId: detail.gatewayId,
					name: detail.name,
				});
			} catch (err) {
				const message = err instanceof Error ? err.message : String(err);
				res.json({ gatewayUrl: "", error: message });
			}
			return;
		}

		const gateways: Array<{
			gatewayId: string;
			name: string;
			status: string;
			description?: string;
			protocolType?: string;
		}> = [];

		let nextToken: string | undefined;
		do {
			const command = new ListGatewaysCommand({
				maxResults: 20,
				...(nextToken ? { nextToken } : {}),
			});
			const response = await client.send(command);
			if (response.items) {
				for (const gw of response.items) {
					gateways.push({
						gatewayId: gw.gatewayId ?? "",
						name: gw.name ?? "",
						status: gw.status ?? "UNKNOWN",
						description: gw.description,
						protocolType: gw.protocolType,
					});
				}
			}
			nextToken = response.nextToken;
		} while (nextToken);

		res.json({ gateways, region });
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		console.error("Error listing gateways:", message);
		res.json({ gateways: [], error: message });
	}
});

app.get(
	"/identity/credential-providers",
	originValidationMiddleware,
	async (req, res) => {
		try {
			const {
				BedrockAgentCoreControlClient,
				ListOauth2CredentialProvidersCommand,
			} = await import("@aws-sdk/client-bedrock-agentcore-control");

			const client = new BedrockAgentCoreControlClient({});

			const providers: Array<{ name: string; arn: string; type: string }> = [];
			let nextToken: string | undefined;
			do {
				const command = new ListOauth2CredentialProvidersCommand({
					maxResults: 20,
					...(nextToken ? { nextToken } : {}),
				});
				const response = await client.send(command);
				if (response.credentialProviders) {
					for (const cp of response.credentialProviders) {
						providers.push({
							name: cp.name ?? "",
							arn: cp.credentialProviderArn ?? "",
							type: cp.credentialProviderVendor ?? "",
						});
					}
				}
				nextToken = response.nextToken;
			} while (nextToken);

			res.json({ providers });
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			console.error("Error listing credential providers:", message);
			res.json({ providers: [], error: message });
		}
	},
);

app.post(
	"/identity/ensure-workload",
	originValidationMiddleware,
	express.json(),
	async (req, res) => {
		const { name } = req.body;
		if (!name) {
			res.status(400).json({ error: "Missing workload identity name" });
			return;
		}

		try {
			const {
				BedrockAgentCoreControlClient,
				CreateWorkloadIdentityCommand,
				GetWorkloadIdentityCommand,
			} = await import("@aws-sdk/client-bedrock-agentcore-control");

			const client = new BedrockAgentCoreControlClient({});

			try {
				const existing = await client.send(
					new GetWorkloadIdentityCommand({ name }),
				);
				res.json({
					name: existing.name ?? name,
					arn: existing.workloadIdentityArn ?? "",
					created: false,
				});
				return;
			} catch (getError: unknown) {
				const errorName =
					getError instanceof Error ? getError.name : String(getError);
				if (
					errorName !== "ResourceNotFoundException" &&
					errorName !== "NotFoundException"
				) {
					throw getError;
				}
			}

			const createResponse = await client.send(
				new CreateWorkloadIdentityCommand({ name }),
			);
			res.json({
				name: createResponse.name ?? name,
				arn: createResponse.workloadIdentityArn ?? "",
				created: true,
			});
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			console.error("Error ensuring workload identity:", message);
			res.json({ name: null, error: message });
		}
	},
);

app.post(
	"/identity/m2m-token",
	originValidationMiddleware,
	express.json(),
	async (req, res) => {
		const { workloadName, credentialProviderName, scopes } = req.body;

		if (!workloadName || !credentialProviderName) {
			res.status(400).json({
				accessToken: null,
				error: "Missing workloadName or credentialProviderName",
			});
			return;
		}

		try {
			const {
				BedrockAgentCoreClient,
				GetWorkloadAccessTokenCommand,
				GetResourceOauth2TokenCommand,
			} = await import("@aws-sdk/client-bedrock-agentcore");

			const client = new BedrockAgentCoreClient({});

			const tokenResponse = await client.send(
				new GetWorkloadAccessTokenCommand({ workloadName }),
			);
			const workloadAccessToken = tokenResponse.workloadAccessToken;

			if (!workloadAccessToken) {
				res.json({
					accessToken: null,
					error: "No workload access token returned",
				});
				return;
			}

			const resourceResponse = await client.send(
				new GetResourceOauth2TokenCommand({
					workloadIdentityToken: workloadAccessToken,
					resourceCredentialProviderName: credentialProviderName,
					scopes: scopes && scopes.length > 0 ? scopes : undefined,
					oauth2Flow: "M2M",
				}),
			);

			res.json({ accessToken: resourceResponse.accessToken ?? null });
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			console.error("Error obtaining M2M token:", message);
			res.json({ accessToken: null, error: message });
		}
	},
);

const PORT = parseInt(
	process.env.SERVER_PORT || DEFAULT_MCP_PROXY_LISTEN_PORT,
	10,
);
const HOST = process.env.HOST || "localhost";

const server = app.listen(PORT, HOST);
server.on("listening", () => {
	console.log(`⚙️ Proxy server listening on ${HOST}:${PORT}`);
	if (!authDisabled) {
		console.log(
			`🔑 Session token: ${sessionToken}\n   ` +
				`Use this token to authenticate requests or set DANGEROUSLY_OMIT_AUTH=true to disable auth`,
		);
	} else {
		console.log(
			`⚠️  WARNING: Authentication is disabled. This is not recommended.`,
		);
	}
});
server.on("error", (err) => {
	if (err.message.includes(`EADDRINUSE`)) {
		console.error(`❌  Proxy Server PORT IS IN USE at port ${PORT} ❌ `);
	} else {
		console.error(err.message);
	}
	process.exit(1);
});
