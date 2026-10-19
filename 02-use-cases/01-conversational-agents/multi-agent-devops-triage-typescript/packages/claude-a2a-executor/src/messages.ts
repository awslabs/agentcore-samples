import type { Message, Part } from '@a2a-js/sdk';
import { Role } from '@a2a-js/sdk';

/**
 * Helpers for building @a2a-js/sdk v1.0 protocol objects.
 *
 * In v1.0 most fields on Message/Part are required (the types are generated
 * from protobuf), so these helpers centralize the boilerplate of filling
 * empty defaults.
 */

export function textPart(text: string): Part {
  return {
    content: { $case: 'text', value: text },
    metadata: undefined,
    filename: '',
    mediaType: '',
  };
}

export function agentMessage(params: {
  text: string;
  taskId: string;
  contextId: string;
}): Message {
  return {
    messageId: crypto.randomUUID(),
    contextId: params.contextId,
    taskId: params.taskId,
    role: Role.ROLE_AGENT,
    parts: [textPart(params.text)],
    metadata: undefined,
    extensions: [],
    referenceTaskIds: [],
  };
}

/**
 * Extracts the plain-text content of an A2A message. Non-text parts (files,
 * structured data) are ignored — the Claude Agent SDK prompt input is text.
 */
export function extractText(message: Message | undefined): string {
  if (!message) return '';
  return message.parts
    .map((part) => (part.content?.$case === 'text' ? part.content.value : ''))
    .filter((text) => text.length > 0)
    .join('\n');
}
