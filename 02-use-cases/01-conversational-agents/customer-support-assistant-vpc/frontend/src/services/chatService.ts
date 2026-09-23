import type { StreamingEvent } from '../types'

/**
 * Invoke Bedrock AgentCore endpoint with streaming
 * Returns parsed streaming events as JSON objects
 */
export async function* invokeAgentStream(
  agentArn: string,
  region: string,
  sessionId: string,
  bearerToken: string,
  prompt: string,
  actorId: string
): AsyncGenerator<StreamingEvent, void, unknown> {
  const escapedArn = encodeURIComponent(agentArn)
  const url = `https://bedrock-agentcore.${region}.amazonaws.com/runtimes/${escapedArn}/invocations`

  const headers = {
    'Authorization': `Bearer ${bearerToken}`,
    'Content-Type': 'application/json',
    'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sessionId,
  }

  const body = JSON.stringify({
    prompt: prompt,
    actor_id: actorId,
  })

  try {
    console.log('[chatService] Sending request to:', url)
    const response = await fetch(url + '?qualifier=DEFAULT', {
      method: 'POST',
      headers: headers,
      body: body,
    })

    console.log('[chatService] Response status:', response.status, response.statusText)

    if (!response.ok) {
      const errorText = await response.text()
      console.error('[chatService] Error response:', errorText)
      throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`)
    }

    if (!response.body) {
      throw new Error('Response body is null')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    console.log('[chatService] Starting to read stream...')

    while (true) {
      const { done, value } = await reader.read()

      if (done) {
        console.log('[chatService] Stream ended')
        break
      }

      const chunk = decoder.decode(value, { stream: true })
      console.log('[chatService] Raw chunk:', chunk)
      buffer += chunk
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmedLine = line.trim()

        if (trimmedLine === '') {
          continue
        }

        // Log raw line for debugging
        console.log('[RAW LINE]:', trimmedLine)

        // Handle SSE format (data: prefix)
        if (trimmedLine.startsWith('data: ')) {
          const dataContent = trimmedLine.substring(6).trim()

          // Try to parse as JSON
          try {
            const event = JSON.parse(dataContent) as StreamingEvent
            console.log('[PARSED EVENT]:', event, 'Type:', typeof event)
            yield event
          } catch (parseError) {
            console.log('[JSON PARSE ERROR]:', parseError)
            console.log('[DATA CONTENT]:', dataContent)
            // Not JSON, treat as plain text (backward compatibility)
            // Remove quotes that backend might add
            const plainText = dataContent.replace(/^"|"$/g, '')
            console.log('[PLAIN TEXT]:', plainText)
            yield { data: plainText } as StreamingEvent
          }
          continue
        }

        // Try to parse as JSON event (no data: prefix)
        try {
          const event = JSON.parse(trimmedLine) as StreamingEvent
          console.log('[PARSED EVENT]:', event, 'Type:', typeof event)
          yield event
        } catch (parseError) {
          console.log('[JSON PARSE ERROR]:', parseError)
          console.log('[TRIMMED LINE]:', trimmedLine)
          // If not valid JSON, log and skip
          console.warn('Failed to parse streaming event:', trimmedLine, parseError)
        }
      }
    }

    // Process any remaining buffer
    if (buffer.trim()) {
      try {
        const event = JSON.parse(buffer.trim()) as StreamingEvent
        yield event
      } catch (parseError) {
        console.warn('Failed to parse final buffer:', buffer.trim())
      }
    }
  } catch (error) {
    console.error('Error invoking agent endpoint:', error)
    throw error
  }
}
