# OBO (On-Behalf-Of) Training Material

Training material and end-to-end examples for OAuth 2.0 On-Behalf-Of flows with AgentCore Identity, Microsoft Entra ID, and Okta.

## Folder layout

```
obo-training/
├── README.md                                        ← this file
├── 1-obo-with-agentcore-overview.md                 ← what OBO is, why it matters, AgentCore Identity
├── 2-examples-overview.md                           ← examples intro, Entra vs Okta protocol comparison
├── OBO Reference Guide.md                           ← deeper reference (claims, RFC details, edge cases)
├── cheatsheet_entra_vs_okta.md                      ← quick one-page side-by-side
└── 3-examples/
    ├── README.md                                    ← how to run the examples, prerequisites, status
    ├── 01-agent-to-downstream/                      ← UC1: agent does OBO directly (1 hop)
    │   ├── entra/{local,real-world}/
    │   └── okta/{local,real-world}/
    └── 02-agent-via-gateway/                        ← UC2: two OBO hops (agent + Gateway)
        ├── entra/real-world/
        └── okta/real-world/
```

Each example under `3-examples/<use-case>/<flavor>/<mode>/` is self-contained with its own `README.md`, `IDP_SETUP.md`, and (for `real-world/`) `ARCHITECTURE.md` + `LEARNING_GUIDE.md`.

## Reading order

For someone new to OBO:

1. **[`1-obo-with-agentcore-overview.md`](./1-obo-with-agentcore-overview.md)** — what OBO is, the three approaches, how AgentCore Identity implements it, Runtime vs Gateway patterns.
2. **[`2-examples-overview.md`](./2-examples-overview.md)** — what the examples cover, Entra vs Okta protocol comparison tables.
3. **[`3-examples/README.md`](./3-examples/README.md)** — prerequisites, per-example status, and how the `local/` vs `real-world/` modes differ.
4. Pick a specific example and follow its own README:
   - Start simple: [`3-examples/01-agent-to-downstream/entra/local/`](./3-examples/01-agent-to-downstream/entra/local/) or [`okta/local/`](./3-examples/01-agent-to-downstream/okta/local/).
   - Real-world deployment: [`3-examples/01-agent-to-downstream/entra/real-world/`](./3-examples/01-agent-to-downstream/entra/real-world/) or [`okta/real-world/`](./3-examples/01-agent-to-downstream/okta/real-world/).
   - Two-OBO-hop pattern (Gateway): [`3-examples/02-agent-via-gateway/entra/real-world/`](./3-examples/02-agent-via-gateway/entra/real-world/) or [`okta/real-world/`](./3-examples/02-agent-via-gateway/okta/real-world/).

Each example's `IDP_SETUP.md` covers everything the IdP side needs — app registrations, scopes, access policies, and troubleshooting. There is no shared setup doc; setup differs per use case (UC1 needs different scopes than UC2), so it lives with the example that uses it.

## Viewing diagrams

The content files use [Mermaid](https://mermaid.js.org/) diagrams embedded in Markdown. They render natively in GitHub, GitLab, VS Code (built-in Markdown preview or the "Markdown Preview Mermaid Support" extension), and any Mermaid-enabled static site generator.

## Key gotchas

- **Entra ID**: the built-in `MicrosoftOauth2` credential provider supports OBO out of the box. No explicit `onBehalfOfTokenExchangeConfig` needed. Use `CustomOauth2` only when you need protocol-level control (e.g., UC2, where the Gateway target requires `customParameters`).
- **Okta**: always use `CustomOauth2` — the built-in `OktaOauth2` provider does not expose OBO configuration. Okta also requires `subject_token_type` and `audience` passed at exchange time on every `GetResourceOauth2Token` call — these are not stored in the credential provider config, they must be passed at every call. Missing either produces an opaque `missing_token_request_parameter` from Okta's `/token` endpoint.

## License

[MIT](./LICENSE) — use it, share it, fork it.
