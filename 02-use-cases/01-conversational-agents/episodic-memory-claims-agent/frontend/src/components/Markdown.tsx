import { type ReactNode } from 'react';

/**
 * Minimal, dependency-free markdown renderer for chat messages.
 * Supports: headings, bold, italic, inline code, links, bullet/numbered
 * lists, and paragraphs separated by blank lines. Plain text and unknown
 * syntax are rendered verbatim, so it degrades gracefully.
 */

// --- Inline formatting: **bold**, *italic* / _italic_, `code`, [text](url) ---
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // Order matters: code first (so its contents aren't reformatted), then links,
  // then bold, then italic.
  const pattern =
    /(`[^`]+`)|(\[[^\]]+\]\([^)]+\))|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*]+\*)|(_[^_]+_)/g;

  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let i = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    const key = `${keyPrefix}-${i++}`;

    if (token.startsWith('`')) {
      nodes.push(<code key={key} className="md-code">{token.slice(1, -1)}</code>);
    } else if (token.startsWith('[')) {
      const linkMatch = /\[([^\]]+)\]\(([^)]+)\)/.exec(token);
      if (linkMatch) {
        nodes.push(
          <a key={key} href={linkMatch[2]} target="_blank" rel="noopener noreferrer">
            {linkMatch[1]}
          </a>
        );
      }
    } else if (token.startsWith('**') || token.startsWith('__')) {
      nodes.push(<strong key={key}>{renderInline(token.slice(2, -2), key)}</strong>);
    } else {
      nodes.push(<em key={key}>{renderInline(token.slice(1, -1), key)}</em>);
    }

    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes;
}

// Join consecutive non-empty lines (within a block) preserving soft line breaks.
function renderLines(lines: string[], keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  lines.forEach((line, idx) => {
    if (idx > 0) out.push(<br key={`${keyPrefix}-br-${idx}`} />);
    out.push(...renderInline(line, `${keyPrefix}-l${idx}`));
  });
  return out;
}

export default function Markdown({ content }: { content: string }) {
  const lines = content.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];

  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Skip blank lines between blocks.
    if (line.trim() === '') {
      i++;
      continue;
    }

    // Headings: #, ##, ###
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const level = Math.min(heading[1].length + 2, 6);
      const inner = renderInline(heading[2], `h-${key}`);
      const props = { key: `h-${key++}`, className: 'md-heading' };
      blocks.push(
        level === 3 ? <h3 {...props}>{inner}</h3> :
        level === 4 ? <h4 {...props}>{inner}</h4> :
        level === 5 ? <h5 {...props}>{inner}</h5> :
        <h6 {...props}>{inner}</h6>
      );
      i++;
      continue;
    }

    // Unordered list: -, *, +  (items may be separated by blank lines)
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length) {
        if (/^\s*[-*+]\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*[-*+]\s+/, ''));
          i++;
        } else if (lines[i].trim() === '' && /^\s*[-*+]\s+/.test(lines[i + 1] ?? '')) {
          i++; // skip blank line between items
        } else {
          break;
        }
      }
      blocks.push(
        <ul key={`ul-${key++}`} className="md-list">
          {items.map((it, idx) => (
            <li key={idx}>{renderInline(it, `ul-${key}-${idx}`)}</li>
          ))}
        </ul>
      );
      continue;
    }

    // Ordered list: 1. 2. ...  (items may be separated by blank lines)
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length) {
        if (/^\s*\d+\.\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*\d+\.\s+/, ''));
          i++;
        } else if (lines[i].trim() === '' && /^\s*\d+\.\s+/.test(lines[i + 1] ?? '')) {
          i++; // skip blank line between items
        } else {
          break;
        }
      }
      blocks.push(
        <ol key={`ol-${key++}`} className="md-list">
          {items.map((it, idx) => (
            <li key={idx}>{renderInline(it, `ol-${key}-${idx}`)}</li>
          ))}
        </ol>
      );
      continue;
    }

    // Table: | col | col | with separator |---|---|
    if (/^\|(.+)\|$/.test(line) && i + 1 < lines.length && /^\|[-:\s|]+\|$/.test(lines[i + 1])) {
      const headerCells = line.split('|').slice(1, -1).map(c => c.trim());
      i += 2; // skip header + separator
      const rows: string[][] = [];
      while (i < lines.length && /^\|(.+)\|$/.test(lines[i])) {
        rows.push(lines[i].split('|').slice(1, -1).map(c => c.trim()));
        i++;
      }
      blocks.push(
        <table key={`tbl-${key++}`} className="md-table">
          <thead>
            <tr>{headerCells.map((c, ci) => <th key={ci}>{renderInline(c, `th-${key}-${ci}`)}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri}>{row.map((c, ci) => <td key={ci}>{renderInline(c, `td-${key}-${ri}-${ci}`)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      );
      continue;
    }

    // Paragraph: gather consecutive non-blank, non-special lines.
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^(#{1,6})\s+/.test(lines[i]) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !/^\|(.+)\|$/.test(lines[i])
    ) {
      para.push(lines[i]);
      i++;
    }
    blocks.push(
      <p key={`p-${key++}`} className="md-paragraph">
        {renderLines(para, `p-${key}`)}
      </p>
    );
  }

  return <>{blocks}</>;
}
