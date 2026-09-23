import { SignatureV4 } from "@smithy/signature-v4";
import { Hash } from "@smithy/hash-node";
import { HttpRequest } from "@smithy/protocol-http";
import { fromNodeProviderChain } from "@aws-sdk/credential-providers";

const HOP_BY_HOP_HEADERS = new Set([
	"connection",
	"keep-alive",
	"proxy-authenticate",
	"proxy-authorization",
	"te",
	"trailer",
	"transfer-encoding",
	"upgrade",
]);

export function createSigV4Fetch(
	region: string,
	service: string,
	innerFetch: (
		input: RequestInfo | URL,
		init?: RequestInit,
	) => Promise<Response>,
): (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> {
	const signer = new SignatureV4({
		credentials: fromNodeProviderChain(),
		region,
		service,
		sha256: Hash.bind(null, "sha256"),
	});

	return async (
		input: RequestInfo | URL,
		init?: RequestInit,
	): Promise<Response> => {
		const url =
			input instanceof URL
				? input
				: input instanceof Request
					? new URL(input.url)
					: new URL(input.toString());

		const method =
			init?.method ?? (input instanceof Request ? input.method : "GET");

		let body: string | undefined;
		if (init?.body) {
			body =
				typeof init.body === "string"
					? init.body
					: Buffer.from(init.body as ArrayBuffer).toString();
		} else if (input instanceof Request && input.body) {
			body = await input.text();
		}

		const headers: Record<string, string> = { host: url.hostname };
		const sourceHeaders =
			init?.headers ?? (input instanceof Request ? input.headers : undefined);

		if (sourceHeaders) {
			const entries =
				sourceHeaders instanceof Headers
					? sourceHeaders.entries()
					: Array.isArray(sourceHeaders)
						? (sourceHeaders as [string, string][]).values()
						: Object.entries(sourceHeaders).values();

			for (const [key, value] of entries) {
				if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
					headers[key.toLowerCase()] = value;
				}
			}
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

		const signedInit: RequestInit = {
			...init,
			method: signed.method,
			headers: signed.headers as Record<string, string>,
			body: signed.body,
		};

		return innerFetch(input, signedInit);
	};
}
