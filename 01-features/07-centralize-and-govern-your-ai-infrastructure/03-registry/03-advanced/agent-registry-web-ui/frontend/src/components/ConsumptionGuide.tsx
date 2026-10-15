// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Tabs from "@cloudscape-design/components/tabs";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Alert from "@cloudscape-design/components/alert";

export interface ConsumptionRecord {
  name?: string;
  recordType?: string;
  descriptors?: any;
}

// ---- helpers ---------------------------------------------------------------

function parseData(branch: any): any {
  const data = branch?.data;
  if (typeof data !== "string") return {};
  try {
    return JSON.parse(data);
  } catch {
    return {};
  }
}

function CodeBlock({ code }: { code: string }) {
  return (
    <div style={{ position: "relative" }}>
      <Box float="right">
        <Button
          iconName="copy"
          variant="inline-icon"
          ariaLabel="Copy to clipboard"
          onClick={() => void navigator.clipboard?.writeText(code)}
        />
      </Box>
      <Box variant="code">
        <pre style={{ whiteSpace: "pre-wrap", margin: 0, overflowX: "auto" }}>
          {code}
        </pre>
      </Box>
    </div>
  );
}

interface Snippet {
  id: string;
  label: string;
  code: string;
  note?: string;
}

// ---- per-type snippet builders --------------------------------------------

function mcpSnippets(rec: ConsumptionRecord): Snippet[] {
  const d = parseData(rec.descriptors?.mcpServer);
  const serverName = d.name || rec.name || "my-mcp-server";
  const localName = String(serverName).split("/").pop() || "server";
  // MCP server.json may express remotes[] (url + transport) or a stdio package.
  const remote = Array.isArray(d.remotes) ? d.remotes[0] : undefined;
  const url = remote?.url || d.url || "https://YOUR-MCP-ENDPOINT/mcp";
  const transport = (
    remote?.type ||
    d.transport ||
    "streamable-http"
  ).toString();

  // Kiro's remote-server entry: url (+ optional headers/oauth). No `type` field.
  const kiroJson = JSON.stringify(
    { mcpServers: { [localName]: { url } } },
    null,
    2,
  );
  // Generic mcpServers block understood broadly by MCP clients.
  const genericJson = JSON.stringify(
    { mcpServers: { [localName]: { type: transport, url } } },
    null,
    2,
  );

  return [
    {
      id: "quick",
      label: "Amazon Quick",
      note:
        "Amazon Quick connects to a remote MCP server through its console (no config file). " +
        "In the Quick console: Connectors → 'Create for your team' → Model Context Protocol (MCP) → " +
        "set MCP server endpoint to the URL below, choose Public network (or a VPC connection for private servers), " +
        "pick the auth method (No authentication / User OAuth / Service), then Create. Quick discovers each tool as an action. " +
        "Requires a Quick Enterprise subscription; remote HTTP-streaming servers only (no stdio).",
      code: `MCP server endpoint:  ${url}
Connection type:      Public network
Authentication:       No authentication   # or User OAuth / Service, per your server`,
    },
    {
      id: "kiro",
      label: "Kiro",
      note:
        "Kiro is a coding agent. Add this to its MCP config: ~/.kiro/settings/mcp.json (user) " +
        "or .kiro/settings/mcp.json (workspace). Kiro hot-reloads on save. For an OAuth-protected server, " +
        'add an "oauth": { … } block (Kiro handles the browser flow; most servers use DCR and need no client id).',
      code: kiroJson,
    },
    {
      id: "generic",
      label: "Generic MCP config",
      note: "The standard mcpServers block understood by MCP-compatible clients.",
      code: genericJson,
    },
    {
      id: "python",
      label: "Python (MCP SDK)",
      code: `# pip install mcp
import asyncio
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

URL = "${url}"

async def main():
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([t.name for t in tools.tools])

asyncio.run(main())`,
    },
    {
      id: "typescript",
      label: "TypeScript (MCP SDK)",
      code: `// npm i @modelcontextprotocol/sdk
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const transport = new StreamableHTTPClientTransport(new URL("${url}"));
const client = new Client({ name: "${localName}-consumer", version: "1.0.0" });
await client.connect(transport);
const tools = await client.listTools();
console.log(tools.tools.map((t) => t.name));`,
    },
  ];
}

function agentSnippets(rec: ConsumptionRecord): Snippet[] {
  const d = parseData(rec.descriptors?.a2aAgentCard);
  const url = d.url || "https://YOUR-AGENT-ENDPOINT/a2a";
  const agentName = d.name || rec.name || "agent";

  return [
    {
      id: "card",
      label: "Fetch agent card",
      note: "A2A agents expose their card at /.well-known/agent-card.json.",
      code: `curl -s ${url.replace(/\/$/, "")}/.well-known/agent-card.json | jq .`,
    },
    {
      id: "python",
      label: "Python (a2a-sdk)",
      code: `# pip install a2a-sdk httpx
import asyncio, httpx
from a2a.client import A2AClient, A2ACardResolver

BASE = "${url}"

async def main():
    async with httpx.AsyncClient() as http:
        card = await A2ACardResolver(http, BASE).get_agent_card()
        client = A2AClient(http, agent_card=card)
        resp = await client.send_message(
            {"role": "user", "parts": [{"kind": "text", "text": "Hello, ${agentName}"}]}
        )
        print(resp)

asyncio.run(main())`,
    },
    {
      id: "typescript",
      label: "TypeScript (@a2a-js/sdk)",
      code: `// npm i @a2a-js/sdk
import { A2AClient } from "@a2a-js/sdk/client";

const client = await A2AClient.fromCardUrl(
  "${url.replace(/\/$/, "")}/.well-known/agent-card.json"
);
const res = await client.sendMessage({
  message: { role: "user", parts: [{ kind: "text", text: "Hello, ${agentName}" }] },
});
console.log(res);`,
    },
  ];
}

function skillSnippets(rec: ConsumptionRecord): Snippet[] {
  const d = parseData(rec.descriptors?.agentSkillsDefinition);
  const repo = d.repository?.url as string | undefined;
  const website = d.websiteUrl as string | undefined;
  const pkg = Array.isArray(d.packages) ? d.packages[0] : undefined;

  const snips: Snippet[] = [];
  if (repo) {
    snips.push({
      id: "clone",
      label: "Clone repository",
      code: `git clone ${repo}`,
    });
  }
  if (pkg) {
    const reg = (pkg.registryType || "").toLowerCase();
    const id = pkg.identifier || rec.name;
    const ver = pkg.version ? `@${pkg.version}` : "";
    const cmd =
      reg === "pypi"
        ? `pip install "${id}${pkg.version ? `==${pkg.version}` : ""}"`
        : reg === "npm"
          ? `npm install ${id}${ver}`
          : `# ${pkg.registryType} package: ${id}${ver}`;
    snips.push({ id: "install", label: "Install package", code: cmd });
  }
  snips.push({
    id: "agentskills",
    label: "Use as an Agent Skill",
    note: "Point your agent framework's skills loader at the skill directory (SKILL.md + assets).",
    code: `# Amazon Bedrock AgentCore / AgentSkills: reference the skill by its
# repository or package, then load it into your agent's skill set.
${repo ? `git clone ${repo} skills/${rec.name}\n` : ""}${website ? `# Docs: ${website}\n` : ""}# See SKILL.md in the skill for its usage contract.`,
  });
  return snips;
}

function customSnippets(rec: ConsumptionRecord): Snippet[] {
  const raw = rec.descriptors?.custom?.data ?? "{}";
  let pretty = raw;
  try {
    pretty = JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    /* keep raw */
  }
  return [
    {
      id: "definition",
      label: "Definition (JSON)",
      note: "Custom records carry an arbitrary JSON definition — consume it however your client expects.",
      code: pretty,
    },
  ];
}

function snippetsFor(rec: ConsumptionRecord): Snippet[] {
  switch (rec.recordType) {
    case "MCP":
      return mcpSnippets(rec);
    case "AGENT":
      return rec.descriptors?.a2aAgentCard
        ? agentSnippets(rec)
        : customSnippets(rec);
    case "SKILL":
      return skillSnippets(rec);
    default:
      return customSnippets(rec);
  }
}

const TYPE_HINT: Record<string, string> = {
  MCP: "Connect any MCP-compatible client to this server.",
  AGENT: "Call this agent over the A2A protocol.",
  SKILL: "Add this skill to your agent.",
  CUSTOM: "Consume this custom resource.",
};

// ---- component -------------------------------------------------------------

export default function ConsumptionGuide({ rec }: { rec: ConsumptionRecord }) {
  const snippets = snippetsFor(rec);
  const hint =
    TYPE_HINT[rec.recordType ?? ""] ?? "How to consume this resource.";

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={`${hint} To let an agent DISCOVER records like this one at runtime, connect your tool to the registry itself — see "Connect your tools" on the registry page.`}
        >
          How to consume
        </Header>
      }
    >
      {snippets.length === 0 ? (
        <Alert type="info">
          No consumption details available for this record.
        </Alert>
      ) : (
        <Tabs
          tabs={snippets.map((s) => ({
            id: s.id,
            label: s.label,
            content: (
              <SpaceBetween size="s">
                {s.note && (
                  <Box color="text-body-secondary" fontSize="body-s">
                    {s.note}
                  </Box>
                )}
                <CodeBlock code={s.code} />
              </SpaceBetween>
            ),
          }))}
        />
      )}
    </Container>
  );
}
