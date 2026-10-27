import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";

import { HttpAgent } from "@ag-ui/client";
import { NextRequest } from "next/server";
import { auth } from "auth";

// 1. You can use any service adapter here for multi-agent support. We use
//    the empty adapter since we're only using one agent.
const serviceAdapter = new ExperimentalEmptyAdapter();

// 2. Build a Next.js API route that handles the CopilotKit runtime requests.
export const POST = async (req: NextRequest) => {
  // Get the session and access token
  const session = await auth();
  const accessToken = session?.accessToken;
  
  // Log for troubleshooting
  console.log('Session:', session);
  console.log('Access Token:', accessToken);

  // Create the CopilotRuntime instance with dynamic access token
  const runtime = new CopilotRuntime({
    agents: {
      // Our FastAPI endpoint URL
      strands_agent: new HttpAgent({ 
        url: process.env.STRANDS_AGENT_URL || "http://localhost:8000",
        headers: {
          "Authorization": accessToken ? `Bearer ${accessToken}` : "",
        },
      }),
    },
  });

  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter,
    endpoint: "/api/copilotkit",
  });

  return handleRequest(req);
};
