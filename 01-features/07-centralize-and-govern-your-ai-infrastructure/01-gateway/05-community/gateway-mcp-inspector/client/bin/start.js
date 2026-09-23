#!/usr/bin/env node

import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { randomBytes } from "crypto";
import { spawn, exec } from "child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DEFAULT_MCP_PROXY_LISTEN_PORT = "6277";

function delay(ms) {
	return new Promise((resolve) => setTimeout(resolve, ms, true));
}

function openBrowser(url) {
	const platform = process.platform;
	if (platform === "darwin") exec(`open "${url}"`);
	else if (platform === "win32") exec(`start "${url}"`);
	else exec(`xdg-open "${url}"`);
}

function getClientUrl(port, authDisabled, sessionToken, serverPort) {
	const host = process.env.HOST || "localhost";
	const baseUrl = `http://${host}:${port}`;

	const params = new URLSearchParams();
	if (serverPort && serverPort !== DEFAULT_MCP_PROXY_LISTEN_PORT) {
		params.set("MCP_PROXY_PORT", serverPort);
	}
	if (!authDisabled) {
		params.set("MCP_PROXY_AUTH_TOKEN", sessionToken);
	}
	return params.size > 0 ? `${baseUrl}/?${params.toString()}` : baseUrl;
}

function spawnProcess(command, args, options) {
	const child = spawn(command, args, {
		...options,
		stdio: options.echoOutput ? ["ignore", "inherit", "inherit"] : "pipe",
	});
	return child;
}

async function startDevServer(serverOptions) {
	const { SERVER_PORT, CLIENT_PORT, sessionToken, abort } = serverOptions;
	const serverArgs = ["tsx", "watch", "--clear-screen=false", "src/index.ts"];

	const child = spawnProcess("npx", serverArgs, {
		cwd: resolve(__dirname, "../..", "server"),
		env: {
			...process.env,
			SERVER_PORT,
			CLIENT_PORT,
			MCP_PROXY_AUTH_TOKEN: sessionToken,
		},
		signal: abort.signal,
		echoOutput: true,
	});

	await delay(3000);
	return { server: child, serverOk: true };
}

async function startProdServer(serverOptions) {
	const { SERVER_PORT, CLIENT_PORT, sessionToken, abort } = serverOptions;
	const inspectorServerPath = resolve(
		__dirname,
		"../..",
		"server",
		"build",
		"index.js",
	);

	const child = spawnProcess("node", [inspectorServerPath], {
		env: {
			...process.env,
			SERVER_PORT,
			CLIENT_PORT,
			MCP_PROXY_AUTH_TOKEN: sessionToken,
		},
		signal: abort.signal,
		echoOutput: true,
	});

	await delay(2000);
	return { server: child, serverOk: true };
}

async function startDevClient(clientOptions) {
	const { CLIENT_PORT, SERVER_PORT, authDisabled, sessionToken, abort } =
		clientOptions;
	const host = process.env.HOST || "localhost";
	const clientArgs = ["vite", "--port", CLIENT_PORT, "--host", host];

	const child = spawnProcess("npx", clientArgs, {
		cwd: resolve(__dirname, ".."),
		env: { ...process.env, CLIENT_PORT },
		signal: abort.signal,
		echoOutput: true,
	});

	const url = getClientUrl(
		CLIENT_PORT,
		authDisabled,
		sessionToken,
		SERVER_PORT,
	);

	setTimeout(() => {
		console.log(
			`\n🚀 AgentCore Gateway MCP Inspector is up and running at:\n   ${url}\n`,
		);
		if (process.env.MCP_AUTO_OPEN_ENABLED !== "false") {
			console.log("🌐 Opening browser...");
			openBrowser(url);
		}
	}, 3000);

	await new Promise((resolve) => {
		child.on("close", resolve);
		child.on("error", resolve);
	});
}

async function startProdClient(clientOptions) {
	const { CLIENT_PORT, SERVER_PORT, authDisabled, sessionToken, abort } =
		clientOptions;
	const inspectorClientPath = resolve(__dirname, "client.js");

	const url = getClientUrl(
		CLIENT_PORT,
		authDisabled,
		sessionToken,
		SERVER_PORT,
	);

	const child = spawnProcess("node", [inspectorClientPath], {
		env: {
			...process.env,
			CLIENT_PORT,
			INSPECTOR_URL: url,
		},
		signal: abort.signal,
		echoOutput: true,
	});

	await new Promise((resolve) => {
		child.on("close", resolve);
		child.on("error", resolve);
	});
}

async function main() {
	const args = process.argv.slice(2);
	let isDev = args.includes("--dev");

	const CLIENT_PORT = process.env.CLIENT_PORT ?? "6274";
	const SERVER_PORT = process.env.SERVER_PORT ?? DEFAULT_MCP_PROXY_LISTEN_PORT;

	console.log(
		isDev
			? "Starting AgentCore Gateway MCP Inspector in development mode..."
			: "Starting AgentCore Gateway MCP Inspector...",
	);

	const sessionToken =
		process.env.MCP_PROXY_AUTH_TOKEN || randomBytes(32).toString("hex");
	const authDisabled = !!process.env.DANGEROUSLY_OMIT_AUTH;

	const abort = new AbortController();

	let cancelled = false;
	process.on("SIGINT", () => {
		cancelled = true;
		abort.abort();
	});

	let server, serverOk;

	try {
		const serverOptions = { SERVER_PORT, CLIENT_PORT, sessionToken, abort };

		const result = isDev
			? await startDevServer(serverOptions)
			: await startProdServer(serverOptions);

		server = result.server;
		serverOk = result.serverOk;
	} catch (error) {}

	if (serverOk) {
		try {
			const clientOptions = {
				CLIENT_PORT,
				SERVER_PORT,
				authDisabled,
				sessionToken,
				abort,
				cancelled,
			};

			await (isDev
				? startDevClient(clientOptions)
				: startProdClient(clientOptions));
		} catch (e) {
			if (!cancelled || process.env.DEBUG) throw e;
		}
	}

	return 0;
}

main()
	.then((_) => process.exit(0))
	.catch((e) => {
		console.error(e);
		process.exit(1);
	});
