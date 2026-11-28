// AgentCore wraps each yielded JSON string in an SSE data frame. Keep incomplete
// lines across transport chunks, including UTF-8 split boundaries in the caller.
export class RuntimeEvents {
  private pending = '';

  push(chunk: string): Record<string, unknown>[] {
    this.pending += chunk;
    const lines = this.pending.split('\n');
    this.pending = lines.pop() || '';
    return lines.flatMap(line => {
      if (!line.startsWith('data:')) return [];
      let value = JSON.parse(line.slice(5).trim());
      if (typeof value === 'string') value = JSON.parse(value);
      if (value.error) throw new Error(value.error);
      return [value];
    });
  }

  finish(): void {
    if (this.pending.trim()) throw new Error('Truncated Runtime stream');
  }
}
