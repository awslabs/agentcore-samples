import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

export const mcpServerCreate = () => {
  const mcpServer = new McpServer({
    name: "MCP-Server",
    version: "1.0.0"
  });

  mcpServer.registerTool("add",
    {
      title: "Addition Tool",
      description: "Add two numbers",
      inputSchema: { a: z.number(), b: z.number() }
    },
    async ({ a, b }) => ({
      content: [{ type: "text", text: String(a + b) }]
    })
  );

  mcpServer.registerTool("subtract",
    {
      title: "Subtraction Tool",
      description: "Subtracts two numbers",
      inputSchema: { a: z.number(), b: z.number() }
    },
    async ({ a, b }) => ({
      content: [{ type: "text", text: String(a - b) }]
    })
  );
   
  mcpServer.registerPrompt(
    "greeting-prompt",
    {
      title: "Greeting Prompt",
      description: "Prompt stored on MCP Server",
      argsSchema: { name: z.string() }
    },
    ({ name }) => ({
      messages: [{
        role: "user",
        content: {
          type: "text",
          text: `Hello ${name}!`
        }
      }]
    })
  );

  return mcpServer
};

