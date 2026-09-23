import http from "node:http";
import zlib from "node:zlib";
import { Agent, tool } from "@strands-agents/sdk";
import { BedrockModel } from "@strands-agents/sdk/models/bedrock";
import z from "zod";

const model = new BedrockModel({
  region: process.env.AWS_REGION || "us-east-1",
  modelId: "global.anthropic.claude-haiku-4-5-20251001-v1:0",
});

const calculator = tool({
  name: "calculator",
  description: "Perform basic arithmetic: add, subtract, multiply, divide.",
  inputSchema: z.object({
    operation: z.enum(["add", "subtract", "multiply", "divide"]),
    a: z.number(),
    b: z.number(),
  }),
  callback: ({ operation, a, b }) => {
    if (operation === "add") return `${a + b}`;
    if (operation === "subtract") return `${a - b}`;
    if (operation === "multiply") return `${a * b}`;
    if (operation === "divide") return b === 0 ? "Error: division by zero" : `${a / b}`;
    return "Unknown operation";
  },
});

const agent = new Agent({ model, tools: [calculator] });

function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    const stream = req.headers["content-encoding"] === "gzip" ? req.pipe(zlib.createGunzip()) : req;
    stream.on("data", (c: Buffer) => chunks.push(c));
    stream.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
    stream.on("error", reject);
  });
}

http.createServer(async (req, res) => {
  if (req.url === "/ping") {
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end('{"status":"Healthy"}');
  }

  if (req.method === "POST" && req.url === "/invocations") {
    let prompt = "Hello";
    try {
      const raw = await readBody(req);
      try {
        const body = JSON.parse(raw);
        if (body.prompt) prompt = body.prompt;
      } catch {
        const text = raw.trim();
        if (text) prompt = text;
      }
    } catch {
      // use default prompt
    }

    const result = await agent.invoke(prompt);
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ response: result.lastMessage }));
  }

  res.writeHead(404);
  res.end();
}).listen(8080, "0.0.0.0");
