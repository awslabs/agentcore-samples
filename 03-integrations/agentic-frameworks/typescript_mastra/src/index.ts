import express, { type Request, type Response } from 'express';
import dotenv from 'dotenv';
import { mastra } from './mastra/index.js';

dotenv.config();

const app = express();
const PORT = process.env.PORT || 8080;

// Disable Express compression for true streaming
app.set('x-powered-by', false);

// Middleware
app.use(express.json());

/**
 * POST /invocations - Main agent interaction endpoint
 *
 * Required Headers:
 * - X-Amzn-Bedrock-AgentCore-Runtime-SessionId: Session ID (required)
 *
 * Optional Headers:
 * - X-Amzn-Bedrock-AgentCore-Runtime-RequestId: Request ID
 * - x-amzn-bedrock-agentcore-runtime-workload-accesstoken: Access token
 */
app.post('/invocations', async (req: Request, res: Response) => {
  try {
    const sessionId = req.headers['x-amzn-bedrock-agentcore-runtime-session-id'] as string;
    const requestId = req.headers['x-amzn-requestid'] as string;
    const accessToken = req.headers['x-amzn-bedrock-agentcore-runtime-workload-accesstoken'] as string;

    console.log('Received request - Session ID:', sessionId);
    if (requestId) console.log('Request ID:', requestId);
    if (accessToken) console.log('Access Token: [REDACTED]');

    // Validate required header
    if (!sessionId) {
      return res.status(400).json({
        error: 'Missing required header: x-amzn-bedrock-agentcore-runtime-session-id'
      });
    }

    // Validate request body
    const { prompt } = req.body;
    if (!prompt) {
      return res.status(400).json({
        error: 'Missing required field: prompt'
      });
    }

    console.log('Prompt:', prompt);

    // Get the utility agent from Mastra
    const agent = mastra.getAgent('utilityAgent');

    if (!agent) {
      console.error('Agent not found: utility-agent');
      return res.status(500).json({
        error: 'Agent not available',
        message: 'The utility-agent could not be loaded'
      });
    }

    // Stream response using the Mastra agent
    console.log('Streaming response with Mastra agent...');

    const stream = await agent.stream(prompt, {
      maxSteps: 5, // Allow up to 5 steps for tool use
    });

    console.log('Stream started');

    // Stream the response chunks to the client immediately
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    for await (const chunk of stream.textStream) {
      res.write(chunk);
      // Force flush the chunk immediately (if the connection supports it)
      if (typeof (res as any).flush === 'function') {
        (res as any).flush();
      }
    }

    console.log('Stream completed');
    res.end();
  } catch (error) {
    console.error('Error processing request:', error);
    res.status(500).json({
      error: 'Internal server error',
      message: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});

/**
 * GET /ping - Health check endpoint
 *
 * Returns:
 * - status: "healthy" or "healthyBusy"
 * - timeOfLastUpdate: Unix timestamp in seconds
 */
app.get('/ping', (_req: Request, res: Response) => {
  res.json({
    status: 'healthy',
    timeOfLastUpdate: Math.floor(Date.now() / 1000)
  });
});

// Start server
app.listen(PORT, () => {
  console.log(`🚀 AgentCore Runtime server listening on port ${PORT}`);
  console.log(`📍 Endpoints:`);
  console.log(`   POST http://0.0.0.0:${PORT}/invocations`);
  console.log(`   GET  http://0.0.0.0:${PORT}/ping`);
});
