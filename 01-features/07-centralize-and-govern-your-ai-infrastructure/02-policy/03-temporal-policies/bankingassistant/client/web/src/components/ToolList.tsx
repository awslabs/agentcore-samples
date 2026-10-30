import type { McpToolInfo } from "../types";

export function ToolList({
  tools,
  onRefresh,
}: {
  tools: McpToolInfo[];
  onRefresh: () => void;
}) {
  return (
    <details className="tool-list-panel">
      <summary>
        Available tools ({tools.length})
        <button
          className="refresh-btn"
          onClick={(e) => {
            e.preventDefault();
            onRefresh();
          }}
          title="Re-fetch the tool list from the gateway"
        >
          ↻
        </button>
      </summary>
      {tools.length === 0 && (
        <div className="muted">No tools available. Click ↻ to refresh.</div>
      )}
      <ul className="tool-list">
        {tools.map((t) => (
          <li key={t.name}>
            <code>{t.name}</code>
            {t.description && <span className="tool-desc">{t.description}</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}
