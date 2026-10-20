/**
 * HR Assistant Agent — TypeScript LangGraph agent deployed on Bedrock AgentCore Runtime.
 *
 * Framework:       LangGraph (@langchain/langgraph) with ChatBedrockConverse (@langchain/aws)
 * Instrumentation: OpenInference (@arizeai/openinference-instrumentation-langchain)
 * Scope name:      @arizeai/openinference-instrumentation-langchain
 *
 * Tools (deterministic mock data for reproducible evaluations):
 *   get_pto_balance       – remaining PTO days for an employee
 *   submit_pto_request    – request time off
 *   lookup_hr_policy      – company HR policy documents
 *   get_benefits_summary  – health, dental, vision, 401k, life insurance details
 *   get_pay_stub          – pay stub for a given period
 *
 * HTTP server (AgentCore Runtime protocol):
 *   POST /invocations – receives {"prompt": "..."}, returns SSE stream
 *   GET  /ping        – health check, returns {"status": "Healthy"}
 */

// ---------------------------------------------------------------------------
// 1. CloudWatch Logs span exporter — writes OTLP-format span documents
//    directly to the runtime log group so AgentCore Evaluations can pick them up.
// ---------------------------------------------------------------------------

import {
  CloudWatchLogsClient,
  CreateLogStreamCommand,
  PutLogEventsCommand,
} from "@aws-sdk/client-cloudwatch-logs";

import {
  type SpanExporter,
  type ReadableSpan,
} from "@opentelemetry/sdk-trace-base";

import { ExportResult, ExportResultCode } from "@opentelemetry/core";

const CW_LOG_GROUP =
  process.env.OTEL_LOG_GROUP_NAME ||
  (process.env.AGENT_RUNTIME_ID
    ? `/aws/bedrock-agentcore/runtimes/${process.env.AGENT_RUNTIME_ID}-DEFAULT`
    : "");

const REGION = process.env.AWS_REGION || "us-east-1";
const SERVICE_NAME = process.env.OTEL_SERVICE_NAME || "hr-assistant-ts";

// Resource attributes written on every span document, mirroring what the ADOT
// sidecar adds for code-zip deployments. Batch and online evaluation discover
// sessions by `service.name` (the runtime's "<name>.DEFAULT" service), so spans
// without these attributes are only reachable by explicit session ID.
// deploy.py injects OTEL_SERVICE_NAME and AGENT_RUNTIME_ARN after creating the runtime.
const RESOURCE_ATTRIBUTES: Record<string, string> = {
  "service.name": SERVICE_NAME,
  "aws.local.service": SERVICE_NAME,
  "aws.service.type": "gen_ai_agent",
  "aws.log.group.names": CW_LOG_GROUP,
  "cloud.provider": "aws",
  "cloud.platform": "aws_bedrock_agentcore",
  "cloud.region": REGION,
  "telemetry.sdk.language": "nodejs",
  ...(process.env.AGENT_RUNTIME_ARN
    ? {
        "cloud.resource_id": `${process.env.AGENT_RUNTIME_ARN}/runtime-endpoint/DEFAULT:DEFAULT`,
      }
    : {}),
};

/**
 * Convert hrTime [seconds, nanoseconds] to a nanosecond timestamp as a number.
 * Note: values >2^53 have reduced precision but are acceptable for evaluator timestamps.
 */
function toNano(hrTime: [number, number]): number {
  return hrTime[0] * 1_000_000_000 + hrTime[1];
}

const SPAN_KIND_NAMES: Record<number, string> = {
  0: "INTERNAL",
  1: "SERVER",
  2: "CLIENT",
  3: "PRODUCER",
  4: "CONSUMER",
};

const STATUS_CODE_NAMES: Record<number, string> = {
  0: "UNSET",
  1: "OK",
  2: "ERROR",
};

/**
 * Writes OTel spans to CloudWatch Logs in the compact flat format that
 * AgentCore EvaluationClient's CloudWatch Logs Insights query understands:
 *   { scope: {name}, name, spanId, traceId, attributes: {flat dict}, ... }
 *
 * This is the same format used by aws/spans log group for Strands agents.
 */
class CloudWatchLogsSpanExporter implements SpanExporter {
  private readonly _client: CloudWatchLogsClient;
  private readonly _logGroup: string;
  private readonly _streamName: string;
  private _streamCreated = false;

  constructor(logGroup: string, region: string) {
    this._client = new CloudWatchLogsClient({ region });
    this._logGroup = logGroup;
    this._streamName = `otel-spans/${new Date()
      .toISOString()
      .slice(0, 10)}/${process.pid}`;
  }

  private async _ensureStream(): Promise<void> {
    if (this._streamCreated) return;
    try {
      await this._client.send(
        new CreateLogStreamCommand({
          logGroupName: this._logGroup,
          logStreamName: this._streamName,
        }),
      );
    } catch (e: unknown) {
      // AWS SDK v3 puts the error code in .name, not .message
      const name = (e as { name?: string }).name ?? "";
      const msg = (e as Error).message ?? "";
      if (
        !name.includes("AlreadyExists") &&
        !msg.toLowerCase().includes("already exists")
      ) {
        throw e;
      }
    }
    this._streamCreated = true;
  }

  export(
    spans: ReadableSpan[],
    resultCallback: (result: ExportResult) => void,
  ): void {
    if (!this._logGroup || spans.length === 0) {
      resultCallback({ code: ExportResultCode.SUCCESS });
      return;
    }

    this._doExport(spans)
      .then(() => resultCallback({ code: ExportResultCode.SUCCESS }))
      .catch((err) => {
        console.error("[CWLSpanExporter] export error:", err);
        resultCallback({ code: ExportResultCode.FAILED, error: err as Error });
      });
  }

  private async _doExport(spans: ReadableSpan[]): Promise<void> {
    await this._ensureStream();

    const logEvents = spans.map((span) => {
      const attrs = { ...span.attributes };

      // Span events — preserve for gen_ai.* events needed by evaluator
      const events = (span.events || []).map((ev) => ({
        name: ev.name,
        timeUnixNano: toNano(ev.time as [number, number]),
        attributes: { ...(ev.attributes ?? {}) },
      }));

      // Compact flat format: top-level spanId, traceId, attributes as plain dict.
      // CloudWatch Logs Insights can query `attributes.session.id` directly.
      // Field types must match ADOTDocumentBuilder expectations:
      //   kind → string name ("INTERNAL", "SERVER", etc.)
      //   startTimeUnixNano / endTimeUnixNano → number (nanoseconds since epoch)
      //   status.code → string name ("OK", "ERROR", "UNSET")
      const doc = {
        resource: { attributes: RESOURCE_ATTRIBUTES },
        scope: { name: "@arizeai/openinference-instrumentation-langchain" },
        name: span.name,
        spanId: span.spanContext().spanId,
        traceId: span.spanContext().traceId,
        parentSpanId: span.parentSpanContext?.spanId ?? "",
        flags: span.spanContext().traceFlags,
        kind: SPAN_KIND_NAMES[span.kind] ?? "INTERNAL",
        startTimeUnixNano: toNano(span.startTime as [number, number]),
        endTimeUnixNano: toNano(span.endTime as [number, number]),
        durationNano:
          toNano(span.endTime as [number, number]) -
          toNano(span.startTime as [number, number]),
        attributes: attrs,
        status: { code: STATUS_CODE_NAMES[span.status.code] ?? "UNSET" },
        events,
      };

      return {
        timestamp: Date.now(),
        message: JSON.stringify(doc),
      };
    });

    const BATCH = 500;
    for (let i = 0; i < logEvents.length; i += BATCH) {
      await this._client.send(
        new PutLogEventsCommand({
          logGroupName: this._logGroup,
          logStreamName: this._streamName,
          logEvents: logEvents.slice(i, i + BATCH),
        }),
      );
    }
  }

  async forceFlush(): Promise<void> {}

  async shutdown(): Promise<void> {}
}

// ---------------------------------------------------------------------------
// 2. OpenTelemetry setup — must run before any LangChain code executes
// ---------------------------------------------------------------------------

import {
  NodeTracerProvider,
  BatchSpanProcessor,
  type SpanProcessor,
  type Span as SDKSpan,
} from "@opentelemetry/sdk-trace-node";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { LangChainInstrumentation } from "@arizeai/openinference-instrumentation-langchain";
import {
  context,
  trace,
  createContextKey,
  type Context,
} from "@opentelemetry/api";

// Session ID context key — set per-request so the processor stamps every span.
const SESSION_ID_CTX_KEY = createContextKey("hr-assistant-session-id");

/** Adds session.id to every span created within the request context. */
class SessionIdSpanProcessor implements SpanProcessor {
  onStart(span: SDKSpan, parentContext: Context): void {
    const sid = parentContext.getValue(SESSION_ID_CTX_KEY) as
      string | undefined;
    if (sid) {
      span.setAttribute("session.id", sid);
    }
  }
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  onEnd(_span: ReadableSpan): void {}
  forceFlush(): Promise<void> {
    return Promise.resolve();
  }
  shutdown(): Promise<void> {
    return Promise.resolve();
  }
}

const cwlExporter = CW_LOG_GROUP
  ? new CloudWatchLogsSpanExporter(CW_LOG_GROUP, REGION)
  : null;

const tracerProvider = new NodeTracerProvider({
  resource: resourceFromAttributes(RESOURCE_ATTRIBUTES),
  spanProcessors: [
    new SessionIdSpanProcessor(),
    ...(cwlExporter ? [new BatchSpanProcessor(cwlExporter)] : []),
  ],
});
tracerProvider.register();

registerInstrumentations({
  instrumentations: [new LangChainInstrumentation()],
});

const tracer = trace.getTracer("hr-assistant-ts");

// ---------------------------------------------------------------------------
// 3. Mock HR data (deterministic — keeps evaluation assertions stable)
// ---------------------------------------------------------------------------

const PTO_BALANCES: Record<
  string,
  { total_days: number; used_days: number; remaining_days: number }
> = {
  "EMP-001": { total_days: 15, used_days: 5, remaining_days: 10 },
  "EMP-002": { total_days: 15, used_days: 12, remaining_days: 3 },
  "EMP-042": { total_days: 20, used_days: 7, remaining_days: 13 },
};

const HR_POLICIES: Record<string, string> = {
  pto:
    "PTO Policy: Full-time employees accrue 15 days of PTO per year (20 days after 3 years). " +
    "PTO requests must be submitted at least 2 business days in advance. " +
    "Unused PTO up to 5 days rolls over to the next year. " +
    "PTO cannot be taken in advance of accrual.",
  remote_work:
    "Remote Work Policy: Employees may work remotely up to 3 days per week with manager approval. " +
    "Core collaboration hours are 10am-3pm local time. " +
    "A dedicated workspace with reliable internet (25 Mbps+) is required. " +
    "Employees must be reachable via Slack and email during core hours.",
  parental_leave:
    "Parental Leave Policy: Primary caregivers receive 16 weeks of fully paid parental leave. " +
    "Secondary caregivers receive 6 weeks of fully paid parental leave. " +
    "Leave may begin up to 2 weeks before the expected birth or adoption date. " +
    "Benefits continue unchanged during parental leave.",
  code_of_conduct:
    "Code of Conduct: All employees are expected to treat colleagues, customers, and partners " +
    "with respect and professionalism. Harassment, discrimination, and retaliation of any kind " +
    "are strictly prohibited. Violations should be reported to HR or via the anonymous hotline.",
};

const BENEFITS: Record<string, string> = {
  health:
    "Health Insurance: The company covers 90% of premiums for employee-only coverage and 75% " +
    "for family coverage. Plans available: Blue Shield PPO, Kaiser HMO, and HDHP with HSA. " +
    "Annual deductible: $500 (PPO), $0 (HMO), $1,500 (HDHP). " +
    "Open enrollment is each November for the following calendar year.",
  dental:
    "Dental Insurance: 100% coverage for preventive care (cleanings, X-rays). " +
    "80% coverage for basic restorative care (fillings, extractions). " +
    "50% coverage for major restorative care (crowns, bridges). " +
    "Annual maximum benefit: $2,000 per person. Orthodontia lifetime maximum: $1,500.",
  vision:
    "Vision Insurance: Annual eye exam covered in full. " +
    "Frames or contacts allowance: $200 per year. " +
    "Laser vision correction discount: 15% off at participating providers.",
  "401k":
    "401(k) Plan: The company matches 100% of employee contributions up to 4% of salary. " +
    "An additional 50% match on the next 2% (total effective match up to 5%). " +
    "Employees are eligible to contribute immediately; company match vests over 3 years. " +
    "2026 IRS contribution limit: $23,500 (under 50), $31,000 (age 50+).",
  life_insurance:
    "Life Insurance: Basic life insurance of 2x annual salary provided at no cost. " +
    "Employees may purchase supplemental coverage up to 5x salary during open enrollment. " +
    "Accidental death and dismemberment (AD&D) coverage equal to basic life benefit is included.",
};

const PAY_STUBS: Record<
  string,
  {
    gross_pay: number;
    federal_tax: number;
    state_tax: number;
    social_security: number;
    medicare: number;
    health_premium: number;
    contribution_401k: number;
    net_pay: number;
    period: string;
  }
> = {
  "EMP-001:2025-12": {
    gross_pay: 8333.33,
    federal_tax: 1458.33,
    state_tax: 416.67,
    social_security: 516.67,
    medicare: 120.83,
    health_premium: 125.0,
    contribution_401k: 333.33,
    net_pay: 5362.5,
    period: "December 2025",
  },
  "EMP-001:2026-01": {
    gross_pay: 8333.33,
    federal_tax: 1458.33,
    state_tax: 416.67,
    social_security: 516.67,
    medicare: 120.83,
    health_premium: 125.0,
    contribution_401k: 333.33,
    net_pay: 5362.5,
    period: "January 2026",
  },
  "EMP-042:2026-01": {
    gross_pay: 10416.67,
    federal_tax: 1875.0,
    state_tax: 520.83,
    social_security: 645.83,
    medicare: 151.04,
    health_premium: 200.0,
    contribution_401k: 416.67,
    net_pay: 6607.3,
    period: "January 2026",
  },
};

let ptoCtr = 0;

// ---------------------------------------------------------------------------
// 4. LangGraph tools
// ---------------------------------------------------------------------------

import { tool } from "@langchain/core/tools";
import { z } from "zod";

const getPtoBalance = tool(
  ({ employee_id }: { employee_id: string }) => {
    const bal = PTO_BALANCES[employee_id];
    if (bal) return JSON.stringify({ employee_id, ...bal });
    return JSON.stringify({
      employee_id,
      error: `Employee ${employee_id} not found.`,
    });
  },
  {
    name: "get_pto_balance",
    description:
      "Return the current PTO balance for an employee. " +
      "Args: employee_id (string, e.g. EMP-001).",
    schema: z.object({
      employee_id: z.string().describe("Employee identifier, e.g. EMP-001"),
    }),
  },
);

const submitPtoRequest = tool(
  ({
    employee_id,
    start_date,
    end_date,
    reason,
  }: {
    employee_id: string;
    start_date: string;
    end_date: string;
    reason?: string;
  }) => {
    ptoCtr += 1;
    const request_id = `PTO-2026-${String(ptoCtr).padStart(3, "0")}`;
    return JSON.stringify({
      request_id,
      employee_id,
      start_date,
      end_date,
      reason: reason ?? "Personal time off",
      status: "APPROVED",
      message: `PTO request ${request_id} approved for ${employee_id} from ${start_date} to ${end_date}.`,
    });
  },
  {
    name: "submit_pto_request",
    description:
      "Submit a PTO request for an employee. " +
      "Args: employee_id, start_date (YYYY-MM-DD), end_date (YYYY-MM-DD), reason (optional).",
    schema: z.object({
      employee_id: z.string().describe("Employee identifier"),
      start_date: z.string().describe("First day of leave in YYYY-MM-DD"),
      end_date: z.string().describe("Last day of leave in YYYY-MM-DD"),
      reason: z.string().optional().describe("Optional reason for the request"),
    }),
  },
);

const lookupHrPolicy = tool(
  ({ topic }: { topic: string }) => {
    const key = topic.toLowerCase().replace(/[\s-]+/g, "_");
    const text = HR_POLICIES[key];
    if (text) return JSON.stringify({ topic, policy_text: text });
    return JSON.stringify({
      topic,
      error: `Policy '${topic}' not found. Available: ${Object.keys(HR_POLICIES).join(", ")}`,
    });
  },
  {
    name: "lookup_hr_policy",
    description:
      "Look up a company HR policy document by topic. " +
      "Supported topics: pto, remote_work, parental_leave, code_of_conduct.",
    schema: z.object({
      topic: z.string().describe("Policy topic, e.g. pto, remote_work"),
    }),
  },
);

const getBenefitsSummary = tool(
  ({ benefit_type }: { benefit_type: string }) => {
    const key = benefit_type.toLowerCase().replace(/[\s-]+/g, "_");
    const text = BENEFITS[key];
    if (text) return JSON.stringify({ benefit_type, summary: text });
    return JSON.stringify({
      benefit_type,
      error: `Benefit '${benefit_type}' not found. Available: ${Object.keys(BENEFITS).join(", ")}`,
    });
  },
  {
    name: "get_benefits_summary",
    description:
      "Return a summary of a specific employee benefit. " +
      "Supported types: health, dental, vision, 401k, life_insurance.",
    schema: z.object({
      benefit_type: z.string().describe("Benefit type, e.g. health, 401k"),
    }),
  },
);

const getPayStub = tool(
  ({ employee_id, period }: { employee_id: string; period: string }) => {
    const stub = PAY_STUBS[`${employee_id}:${period}`];
    if (stub) return JSON.stringify({ employee_id, ...stub });
    return JSON.stringify({
      employee_id,
      period,
      error: `Pay stub not found for ${employee_id} period ${period}.`,
    });
  },
  {
    name: "get_pay_stub",
    description:
      "Retrieve a pay stub for an employee for a specific pay period. " +
      "Args: employee_id, period (YYYY-MM format, e.g. 2026-01).",
    schema: z.object({
      employee_id: z.string().describe("Employee identifier"),
      period: z.string().describe("Pay period in YYYY-MM format"),
    }),
  },
);

const HR_TOOLS = [
  getPtoBalance,
  submitPtoRequest,
  lookupHrPolicy,
  getBenefitsSummary,
  getPayStub,
];

// ---------------------------------------------------------------------------
// 5. LangGraph ReAct agent
// ---------------------------------------------------------------------------

import { ChatBedrockConverse } from "@langchain/aws";
import { createReactAgent } from "@langchain/langgraph/prebuilt";
import { HumanMessage, SystemMessage } from "@langchain/core/messages";

const SYSTEM_PROMPT = `You are a helpful HR Assistant for Acme Corp.

You help employees with:
- Checking PTO (paid time off) balances
- Submitting PTO requests
- Looking up HR policies (PTO, remote work, parental leave, code of conduct)
- Understanding employee benefits (health, dental, vision, 401k, life insurance)
- Retrieving pay stub information

Always use the available tools to answer questions accurately. Do not make up
policy details, benefit amounts, or pay information — look them up.
Be concise, professional, and friendly.`;

const MODEL_ID = process.env.BEDROCK_MODEL_ID || "us.amazon.nova-lite-v1:0";

let agentGraph: ReturnType<typeof createReactAgent> | null = null;

function getAgentGraph(): ReturnType<typeof createReactAgent> {
  if (!agentGraph) {
    const llm = new ChatBedrockConverse({
      model: MODEL_ID,
      region: REGION,
    });
    agentGraph = createReactAgent({ llm, tools: HR_TOOLS });
  }
  return agentGraph;
}

// ---------------------------------------------------------------------------
// 6. Express HTTP server (AgentCore Runtime protocol)
// ---------------------------------------------------------------------------

import express, { type Request, type Response } from "express";

const app = express();
// AgentCore Runtime may omit Content-Type entirely.
// Use a function for `type` so body-parser always captures the raw bytes
// regardless of whether a Content-Type header is present.
app.use(express.raw({ type: () => true, limit: "10mb" }));

// Session header name from AgentCore Runtime
const SESSION_HEADER = "x-amzn-bedrock-agentcore-runtime-session-id";

app.get("/ping", (_req: Request, res: Response) => {
  res.json({ status: "Healthy" });
});

app.post("/invocations", async (req: Request, res: Response) => {
  const sessionId =
    (req.headers[SESSION_HEADER] as string | undefined) ?? "default";

  // Parse body — Buffer from raw middleware or already-parsed object
  let bodyObj: Record<string, unknown> = {};
  try {
    const raw =
      req.body instanceof Buffer
        ? req.body.toString("utf-8")
        : String(req.body ?? "{}");
    bodyObj = JSON.parse(raw);
  } catch {
    bodyObj = {};
  }
  const prompt: string = (bodyObj.prompt as string | undefined) ?? "";

  console.log(`[invoke] session=${sessionId} prompt=${prompt.slice(0, 80)}`);

  // Set session ID in OTel context so SessionIdSpanProcessor stamps every span.
  const requestCtx = context.active().setValue(SESSION_ID_CTX_KEY, sessionId);

  // Wrap in a root "invoke_agent" span so the evaluator can find it by session.
  const rootSpan = tracer.startSpan(
    "invoke_agent",
    {
      attributes: {
        "session.id": sessionId,
        "openinference.span.kind": "AGENT",
        "gen_ai.system": "langchain",
        // Add gen_ai.user.message event-style data as attribute for span collectors
        "input.value": JSON.stringify([{ text: prompt }]),
      },
    },
    requestCtx,
  );
  // Emit a gen_ai.user.message span event (needed by AgentCore span mappers)
  rootSpan.addEvent("gen_ai.user.message", {
    content: JSON.stringify([{ text: prompt }]),
    role: "user",
  });
  const spanCtx = trace.setSpan(requestCtx, rootSpan);

  let responseText = "";
  try {
    responseText = await context.with(spanCtx, async () => {
      const result = await getAgentGraph().invoke({
        messages: [new SystemMessage(SYSTEM_PROMPT), new HumanMessage(prompt)],
      });
      const lastMsg = result.messages[result.messages.length - 1];
      const raw =
        "content" in lastMsg
          ? (lastMsg as { content: unknown }).content
          : String(lastMsg);
      if (Array.isArray(raw)) {
        return raw
          .map((b) =>
            typeof b === "object" && b !== null && "text" in b
              ? String((b as { text: unknown }).text)
              : String(b),
          )
          .join(" ")
          .trim();
      }
      return String(raw).trim();
    });
    // Nova models can emit <thinking>...</thinking> reasoning in the final
    // message; strip it so users and evaluators only see the answer.
    responseText = responseText
      .replace(/<thinking>[\s\S]*?<\/thinking>/g, "")
      .trim();

    rootSpan.setAttribute("output.value", responseText.slice(0, 2000));
    // Emit gen_ai.choice event for span mappers
    rootSpan.addEvent("gen_ai.choice", {
      message: responseText.slice(0, 2000),
      finish_reason: "end_turn",
    });
    rootSpan.setStatus({ code: 1 }); // OK
  } catch (err) {
    console.error("[invoke] error:", err);
    responseText = "I encountered an error processing your request.";
    rootSpan.setStatus({ code: 2, message: String(err) }); // ERROR
  } finally {
    rootSpan.end();
    // Flush buffered spans so they reach CloudWatch before the
    // microVM may freeze between invocations.
    try {
      await tracerProvider.forceFlush();
    } catch {
      // best-effort flush
    }
  }

  console.log(`[invoke] response=${responseText.slice(0, 80)}`);

  // Return SSE-formatted response (matches Python BedrockAgentCoreApp convention)
  res.setHeader("Content-Type", "text/event-stream");
  res.write(`data: ${JSON.stringify(responseText)}\n\n`);
  res.end();
});

const PORT = parseInt(process.env.PORT ?? "8080", 10);
app.listen(PORT, "0.0.0.0", () => {
  console.log(`HR Assistant (TypeScript/LangGraph) listening on port ${PORT}`);
  console.log(`  Model: ${MODEL_ID}`);
  console.log(`  Region: ${REGION}`);
  console.log(`  OTel service: ${SERVICE_NAME}`);
  console.log(`  CW log group: ${CW_LOG_GROUP || "(not set)"}`);
});
