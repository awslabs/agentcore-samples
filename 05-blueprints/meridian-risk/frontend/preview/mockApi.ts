// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Fetch interception for the palette preview.
 *
 * Mocking at the `fetch` boundary rather than stubbing components means the
 * real AssessmentView / RegistryView / GatewayView / MemoryView run unmodified
 * against the real response shapes — so what you are judging is the actual UI,
 * not a lookalike. Nothing here touches AWS.
 *
 * Fixtures mirror genuine CUST00x output captured from the deployed stack.
 */

const CUSTOMERS = [
  {
    id: "CUST001",
    name: "Acme Corporation Ltd",
    industry: "Manufacturing",
    expected: "APPROVE",
    note: "Clean profile: A rating, no sanctions or PEP exposure",
  },
  {
    id: "CUST002",
    name: "TechStart Innovations Inc",
    industry: "Technology",
    expected: "CONDITIONAL",
    note: "Thin financials: BB rating, net loss, elevated leverage",
  },
  {
    id: "CUST003",
    name: "Global Trading Partners LLC",
    industry: "Import/Export",
    expected: "ESCALATE",
    note: "OFAC partial match, flagged PEP, structuring pattern",
  },
]

const TOOL_NAMES = [
  "get_customer_profile",
  "credit_bureau_report",
  "sanctions_screen",
  "transaction_history",
  "adverse_media_scan",
]

/** Verdicts per customer, so all three semantic colours can be inspected. */
const ASSESSMENTS: Record<string, Record<string, unknown>> = {
  CUST001: {
    customer_id: "CUST001",
    session_id: "preview-session-001",
    assessment_type: "full",
    inference: {
      route: "gateway",
      model_id: "bedrock-mantle/deepseek.v3.1",
      guardrail_id: "EXAMPLEGUARDRAIL",
      guardrail_version: "1",
    },
    policy: {
      mode: "ENFORCE",
      engine_id: "kyc_agentcore_policy_engine-EXAMPLE01",
      authorized_calls: 6,
    },
    prior_assessment_total: 27,
    overall_risk_score: 15,
    risk_level: "low",
    recommendation: "APPROVE",
    summary:
      "Acme Corporation Ltd is approved for onboarding with standard commercial banking facilities. The customer presents investment-grade creditworthiness (750 credit score, 'A' rating) with strong financial metrics including conservative leverage (0.33 D/E ratio), excellent liquidity (2.1 current ratio), and near-perfect payment history (96% on-time). Compliance screening is fully clear across all regulatory dimensions.",
    key_risks: [
      "Moderate facility utilization at 50% of the existing $5M revolving line",
      "Three credit inquiries in the last 12 months — monitor for additional borrowing",
    ],
    conditions: [
      "Standard annual financial statement review",
      "Notify the bank of any change in beneficial ownership above 10%",
    ],
    regulatory_actions: [
      "Schedule the next periodic KYC refresh for 2027-01-15",
      "Retain onboarding documentation per BSA recordkeeping requirements",
    ],
    credit_risk: {
      score: 18,
      level: "low",
      factors: [
        "Strong profitability: $8.5M net income on $75M total assets (11.3% ROA)",
        "Conservative leverage: 0.33 debt-to-equity, well below manufacturing norms",
        "Excellent liquidity: 2.1 current ratio provides substantial working capital cushion",
        "Payment discipline: 48 on-time payments against 2 late, zero defaults",
      ],
      recommendations: [
        "Approve for standard facilities up to $7M based on demonstrated capacity",
        "Annual review cadence is sufficient given the stable profile",
      ],
      narrative:
        "Acme presents an exceptionally strong credit profile. Ratios sit comfortably within investment-grade thresholds and the payment record is near-spotless.",
      _skill: "credit-risk-analysis",
      _tools_granted: [
        "gateway_kyc-tools___get_customer_profile",
        "gateway_kyc-tools___credit_bureau_report",
      ],
      _tools_available: 5,
      _withheld: ["adverse_media_scan", "sanctions_screen", "transaction_history"],
      _tool_calls: [
        "gateway_kyc-tools___get_customer_profile",
        "gateway_kyc-tools___credit_bureau_report",
      ],
    },
    compliance: {
      status: "compliant",
      checks_passed: [
        "Sanctions screening — clear against OFAC, UN, EU, and UK HMT databases",
        "PEP screening — 2 directors and 3 beneficial owners screened, no matches",
        "Beneficial ownership transparency — all owners identified above the 25% threshold",
        "Transaction monitoring — no structuring or round-amount patterns detected",
        "Adverse media — no findings across monitored sources",
      ],
      checks_failed: [],
      regulatory_notes: [
        "KYC documentation complete: Certificate of Incorporation, Articles, Board Resolution, Beneficial Ownership Declaration",
        "AML risk rating: low — standard monitoring applies",
      ],
      edd_required: false,
      narrative:
        "Compliance posture is clean across every dimension. No enhanced due diligence is required and the customer qualifies for straight-through onboarding.",
      _skill: "aml-compliance-screening",
      _tools_granted: [
        "gateway_kyc-tools___get_customer_profile",
        "gateway_kyc-tools___sanctions_screen",
        "gateway_kyc-tools___transaction_history",
        "gateway_kyc-tools___adverse_media_scan",
      ],
      _tools_available: 5,
      _withheld: ["credit_bureau_report"],
      _tool_calls: [
        "gateway_kyc-tools___get_customer_profile",
        "gateway_kyc-tools___sanctions_screen",
        "gateway_kyc-tools___transaction_history",
        "gateway_kyc-tools___adverse_media_scan",
      ],
    },
    tools_invoked: TOOL_NAMES.map((n) => `gateway_kyc-tools___${n}`),
    memory_event_id: "0000001785773513609#6584e22d",
    prior_assessment_count: 5,
  },

  CUST002: {
    customer_id: "CUST002",
    session_id: "preview-session-002",
    assessment_type: "full",
    inference: {
      route: "gateway",
      model_id: "bedrock-mantle/deepseek.v3.1",
      guardrail_id: "EXAMPLEGUARDRAIL",
      guardrail_version: "1",
    },
    policy: {
      mode: "ENFORCE",
      engine_id: "kyc_agentcore_policy_engine-EXAMPLE01",
      authorized_calls: 6,
    },
    prior_assessment_total: 27,
    overall_risk_score: 58,
    risk_level: "medium",
    recommendation: "APPROVE",
    summary:
      "TechStart Innovations presents a clean compliance profile with no sanctions, PEP, or AML concerns, enabling onboarding consideration. However, credit risk is elevated (score 58) due to operating losses, high debt utilization, and thin liquidity. Approve with conditions and a reduced facility limit.",
    key_risks: [
      "Operating loss of $500K in FY2023 — negative earnings reduce debt service capacity",
      "Elevated leverage at 1.14 debt-to-equity against a 15M asset base",
      "Thin liquidity: 1.5 current ratio leaves limited working capital headroom",
      "Payment slippage: 4 late payments against 18 on-time (18% late rate)",
    ],
    conditions: [
      "Reduce the initial facility limit to $2M pending demonstrated profitability",
      "Require quarterly rather than annual financial statements",
      "Covenant: maintain a current ratio at or above 1.4",
      "Covenant: no additional secured borrowing without bank consent",
      "Personal guarantee from the founding shareholders",
      "Re-review at 6 months with a view to raising the limit on positive earnings",
    ],
    regulatory_actions: [
      "Standard KYC refresh in 12 months",
      "Flag for credit watch given the negative earnings trend",
    ],
    credit_risk: {
      score: 58,
      level: "medium",
      factors: [
        "Negative earnings: $500K net loss materially weakens coverage",
        "Leverage at 1.14 D/E is high for a company of this size and stage",
        "Liquidity at 1.5 current ratio is adequate but not comfortable",
        "Payment record shows 4 late payments in 22 total — a deteriorating signal",
        "BB credit rating with a 680 score places this below investment grade",
      ],
      recommendations: [
        "Cap exposure at $2M until two consecutive profitable quarters",
        "Require quarterly reporting and a liquidity covenant",
        "Consider a personal guarantee to offset the earnings risk",
      ],
      narrative:
        "TechStart is a growth-stage technology company with real revenue but no profitability yet. Creditworthy at a reduced limit with tight covenants.",
      _skill: "credit-risk-analysis",
      _tools_granted: [
        "gateway_kyc-tools___get_customer_profile",
        "gateway_kyc-tools___credit_bureau_report",
      ],
      _tools_available: 5,
      _withheld: ["adverse_media_scan", "sanctions_screen", "transaction_history"],
      _tool_calls: [
        "gateway_kyc-tools___get_customer_profile",
        "gateway_kyc-tools___credit_bureau_report",
      ],
    },
    compliance: {
      status: "compliant",
      checks_passed: [
        "Sanctions screening — clear across OFAC, UN, EU, UK HMT",
        "PEP screening — no politically exposed persons among directors or owners",
        "Transaction monitoring — no suspicious patterns; volumes consistent with stated business",
        "Adverse media — no findings",
      ],
      checks_failed: [],
      regulatory_notes: [
        "AML risk rating: low",
        "KYC verification complete; no enhanced due diligence triggered",
      ],
      edd_required: false,
      narrative:
        "No compliance impediment to onboarding. The constraint here is credit, not regulatory.",
      _skill: "aml-compliance-screening",
      _tools_granted: [
        "gateway_kyc-tools___get_customer_profile",
        "gateway_kyc-tools___sanctions_screen",
        "gateway_kyc-tools___transaction_history",
        "gateway_kyc-tools___adverse_media_scan",
      ],
      _tools_available: 5,
      _withheld: ["credit_bureau_report"],
      _tool_calls: [
        "gateway_kyc-tools___get_customer_profile",
        "gateway_kyc-tools___sanctions_screen",
        "gateway_kyc-tools___transaction_history",
        "gateway_kyc-tools___adverse_media_scan",
      ],
    },
    tools_invoked: TOOL_NAMES.map((n) => `gateway_kyc-tools___${n}`),
    memory_event_id: "0000001785773698441#7c91a3f2",
    prior_assessment_count: 5,
  },

  CUST003: {
    customer_id: "CUST003",
    session_id: "preview-session-003",
    assessment_type: "full",
    inference: {
      route: "gateway",
      model_id: "bedrock-mantle/deepseek.v3.1",
      guardrail_id: "EXAMPLEGUARDRAIL",
      guardrail_version: "1",
    },
    policy: {
      mode: "ENFORCE",
      engine_id: "kyc_agentcore_policy_engine-EXAMPLE01",
      authorized_calls: 6,
    },
    prior_assessment_total: 27,
    overall_risk_score: 95,
    risk_level: "critical",
    recommendation: "REJECT",
    summary:
      "Global Trading Partners LLC (CUST003) must be REJECTED for the second consecutive assessment with ZERO improvement in compliance posture. All four disqualifying violations identified in the prior assessment remain entirely unresolved: the OFAC partial sanctions match (65% on 'Rodriguez Trading Co') continues under review since 2024-01-18, creating an absolute bar to onboarding under 31 CFR 501; the structuring pattern of 15 transactions at exactly $99,999 remains mandatory SAR-reportable conduct under 31 CFR 1020.320; PEP exposure via Elena Martinez (40% beneficial owner) still lacks completed enhanced due diligence; and high-risk jurisdiction exposure persists without enhanced monitoring.",
    key_risks: [
      "UNRESOLVED SANCTIONS MATCH (UNCHANGED): OFAC partial match 65% on 'Rodriguez Trading Co' — absolute regulatory bar under 31 CFR 501",
      "MANDATORY SAR OBLIGATION (UNCHANGED): structuring pattern of 15 transactions at exactly $99,999 constitutes reportable conduct under 31 CFR 1020.320",
      "PEP EXPOSURE WITHOUT EDD (UNCHANGED): Elena Martinez (40% beneficial owner) flagged as PEP family member requiring mandatory EDD under 31 CFR 1010.610",
      "HIGH-RISK JURISDICTION EXPOSURE (UNCHANGED): ongoing transactions with Venezuela and Myanmar without an enhanced monitoring framework",
      "CRITICAL CREDIT RISK (NO IMPROVEMENT): credit score static at 620/850 (B rating), excessive leverage at 4.0:1 debt-to-equity",
      "ADVERSE MEDIA UNADDRESSED (UNCHANGED): Financial Times article linking the entity to trade finance irregularities",
    ],
    conditions: [
      "NO CONDITIONS — customer must be categorically rejected; onboarding is prohibited until all compliance violations are fully remediated",
    ],
    regulatory_actions: [
      "MAINTAIN REJECT STATUS — do not onboard under any circumstances until sanctions clearance is obtained",
      "REQUIRE formal OFAC clearance letter definitively resolving the 65% partial match before any future reconsideration",
      "MANDATE completion of enhanced due diligence on Elena Martinez including source of funds and political exposure assessment per 31 CFR 1010.610",
      "DEMAND written explanation of the structuring pattern and confirmation of SAR filing under 31 CFR 1020.320",
      "CONDUCT reputational risk assessment addressing the Financial Times adverse media finding",
    ],
    credit_risk: {
      score: 92,
      level: "critical",
      factors: [
        "COMPLIANCE VIOLATIONS PERSIST: PEP exposure unresolved — Elena Martinez (40% owner), no evidence of mandatory EDD completion",
        "HIGH-RISK GEOGRAPHY ACTIVE: exposure to OFAC-sanctioned and FATF high-risk jurisdictions (Venezuela, Myanmar) without enhanced monitoring",
        "CREDIT PERFORMANCE UNCHANGED: score remains 620/850, identical to the deteriorated level at prior assessment; 1 default still recorded",
        "EXCESSIVE LEVERAGE: 4.0:1 debt-to-equity is 2-3x typical import/export industry norms, indicating over-reliance on borrowed capital",
        "FACILITY UTILIZATION STRESS: trade finance 85% utilized and letter of credit 84% utilized, leaving minimal headroom",
        "ELEVATED CREDIT INQUIRY VELOCITY: 12 inquiries in the last 12 months suggests active search for additional financing — distress signal",
      ],
      recommendations: [
        "REJECT onboarding — critical compliance violations remain unmitigated",
        "Require formal OFAC clearance letter resolving the 65% partial match before reconsideration",
        "Mandate completion of enhanced due diligence on the PEP beneficial owner",
      ],
      narrative:
        "Global Trading Partners LLC remains CRITICAL RISK with score 92/100, UNCHANGED from the prior assessment. The combination of unresolved regulatory bars and persistently weak financial profile makes this customer unsuitable for onboarding.",
      _skill: "credit-risk-analysis",
      _tools_granted: [
        "gateway_kyc-tools___get_customer_profile",
        "gateway_kyc-tools___credit_bureau_report",
      ],
      _tools_available: 5,
      _withheld: ["adverse_media_scan", "sanctions_screen", "transaction_history"],
      _tool_calls: [
        "gateway_kyc-tools___get_customer_profile",
        "gateway_kyc-tools___credit_bureau_report",
      ],
    },
    compliance: {
      status: "non_compliant",
      checks_passed: [
        "Beneficial ownership transparency — both beneficial owners identified: Carlos Mendez (60%), Elena Martinez (40%)",
      ],
      checks_failed: [
        "SANCTIONS EXPOSURE — UNRESOLVED OFAC partial match (65% on 'Rodriguez Trading Co') under review since 2024-01-18, creating an absolute regulatory bar under 31 CFR 501; NO IMPROVEMENT from prior assessment",
        "PEP EXPOSURE — Elena Martinez (40% beneficial owner, Operations Director) flagged as PEP family member related to a former Colombian government official; mandatory EDD required under 31 CFR 1010.610 but NOT COMPLETED",
        "STRUCTURING PATTERN — 15 transactions at exactly $99,999 detected, constituting mandatory SAR-reportable conduct under 31 CFR 1020.320 (deliberate CTR threshold evasion); PATTERN PERSISTS from prior assessment",
        "HIGH-RISK JURISDICTIONS — ongoing transaction exposure to Venezuela and Myanmar (OFAC-sanctioned, FATF high-risk) without an enhanced monitoring framework; RISK UNMITIGATED",
        "ADVERSE MEDIA — Financial Times (2023-09-15) article linking the entity to trade finance irregularities; reputational risk unaddressed",
      ],
      regulatory_notes: [
        "31 CFR 501 (OFAC) — partial sanctions match screened against OFAC, UN, EU, UK HMT databases creates an absolute bar to onboarding until definitively cleared",
        "31 CFR 1020.320 (BSA) — structuring pattern of 15 transactions at $99,999 constitutes a mandatory Suspicious Activity Report filing obligation",
        "31 CFR 1010.610 (CDD Rule) — PEP family member status triggers mandatory enhanced due diligence; no EDD completed to date",
        "FATF Recommendation 10 — high-risk jurisdiction exposure requires enhanced ongoing monitoring and transaction controls not currently in place",
      ],
      edd_required: true,
      narrative:
        "Global Trading Partners LLC remains NON-COMPLIANT with NO MATERIAL IMPROVEMENT since the prior REJECT decision. This customer is categorically unsuitable for onboarding and must remain REJECTED until all compliance deficiencies are fully remediated and sanctions screening is definitively cleared.",
      _skill: "aml-compliance-screening",
      _tools_granted: [
        "gateway_kyc-tools___get_customer_profile",
        "gateway_kyc-tools___sanctions_screen",
        "gateway_kyc-tools___transaction_history",
        "gateway_kyc-tools___adverse_media_scan",
      ],
      _tools_available: 5,
      _withheld: ["credit_bureau_report"],
      _tool_calls: [
        "gateway_kyc-tools___get_customer_profile",
        "gateway_kyc-tools___sanctions_screen",
        "gateway_kyc-tools___transaction_history",
        "gateway_kyc-tools___adverse_media_scan",
      ],
    },
    tools_invoked: TOOL_NAMES.map((n) => `gateway_kyc-tools___${n}`),
    memory_event_id: "0000001785773882997#30669194",
    prior_assessment_count: 5,
  },
}

const MCP_SERVER = JSON.stringify({
  name: "kyc/kyc-gateway",
  version: "1.0.0",
  description: "Corporate KYC data-retrieval tools over MCP (Lambda target, IAM auth)",
  remotes: [
    {
      type: "streamable-http",
      url: "https://kyc-agentcore-gateway-EXAMPLE01.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
    },
  ],
})

const TOOL_DESCRIPTIONS: Record<string, string> = {
  get_customer_profile:
    "Retrieve the corporate customer's legal identity, incorporation details, industry, annual revenue, directors, beneficial owners, and any standing risk flags. Use this first to establish who the entity is before running credit or compliance checks.",
  credit_bureau_report:
    "Retrieve the credit bureau report: credit score and letter rating, existing credit facilities and utilization, payment history (on-time/late/defaults), audited financial statements, and key ratios (debt-to-equity, current ratio, net income).",
  sanctions_screen:
    "Screen the entity, its directors, and its beneficial owners against OFAC, UN, EU, and UK HMT sanctions lists plus PEP databases. Returns match details, KYC document verification status, AML risk rating, and whether enhanced due diligence is required.",
  transaction_history:
    "Retrieve 12-month transaction analytics: total inflow/outflow, average monthly volume, top counterparties with countries, domestic vs international split, exposure to high-risk jurisdictions, and detected suspicious patterns such as structuring.",
  adverse_media_scan:
    "Scan negative news and adverse media sources for the entity and its principals. Returns findings with severity and the date of the most recent check.",
}

const GATEWAY_TOOLS = TOOL_NAMES.map((name) => ({
  name,
  description: TOOL_DESCRIPTIONS[name],
  inputSchema: {
    type: "object",
    description: "Customer lookup parameters",
    properties: {
      customer_id: {
        type: "string",
        description: "Corporate customer identifier, e.g. CUST001",
      },
    },
    required: ["customer_id"],
  },
}))

const REGISTRY_RECORDS = [
  {
    recordId: "bjh2VrxGoscD",
    name: "credit-risk-analysis",
    description:
      "Agent skill: corporate credit risk analysis producing a 0-100 score from financials, bureau data, and payment history.",
    descriptorType: "AGENT_SKILLS",
    status: "APPROVED",
    statusReason:
      "Approved by the FSI platform governance team for the KYC POC.",
    descriptors: {
      agentSkills: {
        skillMd: {
          inlineContent:
            "---\nname: credit-risk-analysis\ndescription: Assesses corporate creditworthiness from audited financials, credit bureau data, and payment history.\n---\n\n# Credit Risk Analysis\n\nEvaluates a corporate customer's ability to service credit obligations.\n",
        },
      },
    },
  },
  {
    recordId: "8LLfAHwh3vEr",
    name: "kyc-gateway",
    description:
      "MCP server providing five KYC data tools: customer profile, credit bureau report, sanctions/PEP screening, transaction history, and adverse media scan.",
    descriptorType: "MCP",
    status: "APPROVED",
    descriptors: {
      mcp: {
        server: { inlineContent: MCP_SERVER },
        tools: { inlineContent: JSON.stringify({ tools: GATEWAY_TOOLS }) },
      },
    },
  },
  {
    recordId: "8O7BunMG9kom",
    name: "kyc-orchestrator",
    description:
      "A2A agent card for the KYC onboarding risk assessor running on AgentCore Runtime.",
    descriptorType: "A2A",
    status: "PENDING_APPROVAL",
    statusReason: "Submitted for governance review.",
    descriptors: {
      a2a: {
        agentCard: {
          inlineContent: JSON.stringify({
            protocolVersion: "0.3.0",
            name: "KYC Onboarding Risk Assessor",
            version: "1.0.0",
          }),
        },
      },
    },
  },
  {
    recordId: "VjrjpKVxGgGG",
    name: "aml-compliance-screening",
    description:
      "Agent skill: KYC/AML clearance via sanctions and PEP screening, transaction monitoring, and adverse media scanning.",
    descriptorType: "AGENT_SKILLS",
    status: "DRAFT",
    descriptors: {
      agentSkills: {
        skillMd: {
          inlineContent:
            "---\nname: aml-compliance-screening\ndescription: Screens corporate customers against sanctions and PEP lists.\n---\n\n# AML and Compliance Screening\n",
        },
      },
    },
  },
]

function memoryFor(customerId: string) {
  const verdicts: Record<string, string> = {
    CUST001: "APPROVE; overall risk score 15/100 (low risk)",
    CUST002: "APPROVE; overall risk score 58/100 (medium risk)",
    CUST003: "REJECT; overall risk score 95/100 (critical risk)",
  }
  const verdict = verdicts[customerId] ?? "APPROVE; overall risk score 20/100"

  const events = [0, 1].map((index) => ({
    eventId: `preview-event-${customerId}-${index}`,
    sessionId: `preview-session-${customerId}-${index}`,
    eventTimestamp: new Date(Date.now() - index * 86_400_000).toISOString(),
    payload: [
      {
        conversational: {
          role: "USER",
          content: {
            text: `KYC onboarding assessment result for ${customerId}: decision ${verdict}. Key risks identified and recorded for the next review.`,
          },
        },
      },
      {
        conversational: {
          role: "ASSISTANT",
          content: {
            text: `Recorded the decision for ${customerId} in the KYC assessment history.`,
          },
        },
      },
    ],
  }))

  return {
    customer_id: customerId,
    memory_id: "kyc_agentcore_kyc_memory-EXAMPLE01",
    session_count: events.length,
    event_count: events.length,
    events,
    records: [
      {
        memoryRecordId: `preview-record-${customerId}`,
        namespaces: [`/kyc/${customerId}/assessments`],
        content: {
          text: `Customer ${customerId} was assessed with decision ${verdict}.`,
        },
      },
    ],
  }
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

/** Build the newline-delimited SSE stream the real AssessmentView parses. */
function assessStream(customerId: string): Response {
  const assessment = ASSESSMENTS[customerId] ?? ASSESSMENTS.CUST001
  const frames = [
    {
      type: "status",
      stage: "dispatch",
      message: `Dispatching ${customerId} to the AgentCore Runtime…`,
    },
    {
      type: "status",
      stage: "recall",
      message: `Recalling prior assessments for ${customerId} from AgentCore Memory...`,
    },
    {
      type: "status",
      stage: "specialists",
      message:
        "Running Credit Analyst and Compliance Officer in parallel against AgentCore Gateway tools...",
    },
    {
      type: "status",
      stage: "synthesis",
      message: "Synthesizing final onboarding recommendation...",
    },
    { type: "result", assessment },
  ]

  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    async start(controller) {
      for (const frame of frames) {
        // The real Runtime double-encodes: the SSE payload is a JSON string
        // whose contents are the event. Reproduced so the parser is exercised.
        const payload = JSON.stringify(JSON.stringify(frame))
        controller.enqueue(encoder.encode(`data: ${payload}\n\n`))
        // Brief pause so the progress trace is visible rather than instant.
        await new Promise((resolve) => setTimeout(resolve, 320))
      }
      controller.close()
    },
  })

  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  })
}

/**
 * Install the mock. Every /api/* and /config.json request is served locally;
 * anything else (fonts, assets) falls through to the real network.
 */
export function installMockApi(): void {
  const realFetch = window.fetch.bind(window)

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url
    const path = url.startsWith("http") ? new URL(url).pathname : url

    // Empty userPoolId means authRequired() is false, so the app skips login.
    if (path.endsWith("/config.json")) {
      return json({
        apiBase: "",
        region: "us-east-1",
        userPoolId: "",
        userPoolClientId: "",
        identityPoolId: "",
      })
    }

    if (path === "/api/config") {
      return json({
        region: "us-east-1",
        runtime_arn:
          "arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/kyc_agentcore_kyc_agent-EXAMPLE01",
        registry_id: "EXAMPLEREGISTRY01",
        gateway_id: "kyc-agentcore-gateway-EXAMPLE01",
        gateway_url:
          "https://kyc-agentcore-gateway-EXAMPLE01.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
        gateway_inference_url:
          "https://kyc-agentcore-gateway-EXAMPLE01.gateway.bedrock-agentcore.us-east-1.amazonaws.com/inference/v1",
        memory_id: "kyc_agentcore_kyc_memory-EXAMPLE01",
        user_pool_id: "us-east-1_EXAMPLE01",
        guardrail_id: "EXAMPLEGUARDRAIL",
        guardrail_version: "1",
        inference_route: "gateway",
        policy_engine_id: "kyc_agentcore_policy_engine-EXAMPLE01",
        policy_mode: "ENFORCE",
        configured: true,
        demo_customers: CUSTOMERS,
      })
    }

    if (path === "/api/assess") {
      const body = JSON.parse(String(init?.body ?? "{}"))
      return assessStream(String(body.customer_id ?? "CUST001"))
    }

    if (path === "/api/registry/records") {
      return json({
        registry_id: "EXAMPLEREGISTRY01",
        count: REGISTRY_RECORDS.length,
        records: REGISTRY_RECORDS,
      })
    }

    if (path === "/api/registry/search") {
      const approved = REGISTRY_RECORDS.filter((r) => r.status === "APPROVED")
      return json({ query: "preview", count: approved.length, records: approved })
    }

    if (path.startsWith("/api/registry/records/") && path.endsWith("/status")) {
      return json({ status: "APPROVED" })
    }

    if (path === "/api/gateway/tools") {
      return json({
        gateway_id: "kyc-agentcore-gateway-EXAMPLE01",
        gateway_url:
          "https://kyc-agentcore-gateway-EXAMPLE01.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
        targets: [
          {
            target_id: "KYCTOOLS01",
            name: "kyc-tools",
            status: "READY",
            tools: GATEWAY_TOOLS,
          },
        ],
        tool_count: GATEWAY_TOOLS.length,
      })
    }

    if (path === "/api/gateway/invoke") {
      const body = JSON.parse(String(init?.body ?? "{}"))
      return json({
        tool: body.tool_name,
        customer_id: body.customer_id,
        result: {
          tool: body.tool_name,
          result: {
            customer_id: body.customer_id,
            sanctions_screening: {
              status: "potential_match",
              last_check: "2024-01-18",
              databases_checked: ["OFAC", "UN", "EU", "UK HMT"],
              matches: [
                {
                  database: "OFAC",
                  match_type: "partial_name",
                  entity: "Rodriguez Trading Co",
                  match_score: 65,
                  status: "under_review",
                },
              ],
            },
            aml_risk_rating: "high",
            enhanced_due_diligence_required: true,
            requires_manual_review: true,
          },
        },
      })
    }

    if (path.startsWith("/api/memory/")) {
      return json(memoryFor(path.split("/").pop()!.toUpperCase()))
    }

    return realFetch(input as RequestInfo, init)
  }
}
