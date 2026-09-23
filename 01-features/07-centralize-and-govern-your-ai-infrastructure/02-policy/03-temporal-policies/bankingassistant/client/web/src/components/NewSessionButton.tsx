import { useState } from "react";

import type { ProtocolVersion } from "../types";

const PROTOCOLS: ProtocolVersion[] = ["2025-11-25", "2026-07-28"];

export function NewSessionButton({
  onCreate,
  disabled = false,
}: {
  onCreate: (protocol: ProtocolVersion, policySessionId?: string) => void;
  disabled?: boolean;
}) {
  const [policyId, setPolicyId] = useState("");

  return (
    <div className="new-session">
      <div className="new-session-label">New session</div>

      <input
        className="policy-id-input"
        type="text"
        value={policyId}
        placeholder="Policy session ID (optional)"
        disabled={disabled}
        onChange={(e) => setPolicyId(e.target.value)}
      />
      <div className="policy-id-hint">
        Leave blank to let the gateway issue one on the first tool call.
      </div>

      <div className="protocol-buttons">
        {PROTOCOLS.map((p) => (
          <button
            key={p}
            className="protocol-btn"
            disabled={disabled}
            onClick={() => onCreate(p, policyId.trim() || undefined)}
            title={`Start a new ${p} session`}
          >
            + {p}
            {p === "2026-07-28" ? " (stateless)" : ""}
          </button>
        ))}
      </div>
    </div>
  );
}
