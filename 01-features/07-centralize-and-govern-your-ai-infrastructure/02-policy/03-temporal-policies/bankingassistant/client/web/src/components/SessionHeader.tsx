import type { SessionDTO } from "../types";

export function SessionHeader({ session }: { session: SessionDTO }) {
  const mcpId =
    session.protocol === "2026-07-28"
      ? "n/a (stateless)"
      : session.mcpSessionId ?? "— (set after connect)";
  const policyId = session.policySessionId ?? "— (captured on first tool call)";

  const source = session.policySessionSource;
  const sourceTag =
    source === "user"
      ? { text: "user-set", cls: "src-user" }
      : source === "gateway"
        ? { text: "gateway-issued", cls: "src-gateway" }
        : null;

  return (
    <div className="session-header">
      <span className={`badge badge-${session.protocol}`}>{session.protocol}</span>
      <div className="id-box">
        <label>MCP Session ID</label>
        <code className={session.mcpSessionId ? "" : "pending"}>{mcpId}</code>
      </div>
      <div className="id-box">
        <label>
          Policy Session ID
          {sourceTag && <span className={`src-tag ${sourceTag.cls}`}>{sourceTag.text}</span>}
        </label>
        <code
          className={`${session.policySessionId ? "" : "pending"} ${
            source === "user" ? "user-set" : ""
          }`}
        >
          {policyId}
        </code>
      </div>
    </div>
  );
}
