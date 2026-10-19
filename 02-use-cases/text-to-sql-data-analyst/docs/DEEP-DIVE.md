# Text-to-SQL with Amazon Bedrock AgentCore — Technical Deep Dive

## 1. Architecture Overview

**Main flow:** User → CloudFront → API Gateway → Lambda → Amazon Bedrock Guardrails → Claude Sonnet 4 (Strands SDK) → Glue/Athena/S3

**Dual memory:** STM (active session) + Semantic Memory (SQL patterns, TTL 90 days)

**Observability:** CloudWatch (logs + custom metrics)

### AWS Services

| Service | Purpose | Estimated Cost |
|---------|---------|---------------|
| CloudFront | CDN for static frontend | ~$0.01/mo (free tier) |
| API Gateway | REST API with CORS | ~$3.50/million requests |
| Lambda | Orchestrator (Python 3.11, 512MB, ARM64) | ~$0.20/million invocations |
| Amazon Bedrock AgentCore | Agent runtime with memory | Included with Amazon Bedrock |
| Claude Sonnet 4 | LLM for SQL generation + responses | ~$3/1M input + $15/1M output tokens |
| Glue Data Catalog | Table and column metastore | Free (first 1M objects) |
| Athena | Serverless SQL engine over S3 | $5/TB scanned |
| S3 | Data Lake (Parquet columnar) | ~$0.023/GB/mo |
| CloudWatch | Logs, metrics, observability | ~$0.50/GB ingested |

### Estimated Monthly Cost (~1,000 queries/month)

| Component | Calculation | Cost |
|-----------|------------|------|
| Claude Sonnet 4 | ~900 tokens/query × 1,000 = 900K tokens | ~$5.40 |
| Athena | ~2KB/query × 1,000 = 2MB scanned | ~$0.01 |
| Lambda | 1,000 invocations × 15s × 512MB | ~$0.10 |
| API Gateway | 1,000 requests | ~$0.004 |
| Keep-alive | 14,400 invocations/mo (every 3 min) | ~$13.00 |
| **Total** | | **~$18.50/mo** |

---

## 2. Detailed Request Flow

### 2.1 First query in session (full flow ~12-15s)

```
Time     Step                          Who decides         Duration
──────   ────                          ───────────         ────────
  0ms    User submits question         Frontend            -
 50ms    CloudFront → API Gateway      Infrastructure      ~50ms
100ms    API Gateway → Lambda          AWS                 ~50ms
150ms    Lambda → AgentCore Runtime    invoke_agent_runtime ~100ms
250ms    AgentCore loads STM Memory    AgentCore           ~200ms
         (session context)
450ms    Claude receives question      Claude Sonnet 4     -
         + system prompt + memory
450ms    Claude decides: "I need       Claude Sonnet 4     ~2,000ms
         the DB schema"
2.5s     Tool call: discover_schema()  Claude → Glue       ~600ms
3.1s     Claude receives schema        Claude Sonnet 4     ~3,000ms
         (3 tables, 23 columns)
         Generates optimized SQL
6.1s     Tool call: execute_query()    Claude → Athena     ~700ms
6.8s     Claude receives results       Claude Sonnet 4     ~2,000ms
         Formats response with
         data + context
8.8s     AgentCore saves to STM        AgentCore Memory    ~200ms
9.0s     Response → Lambda             AgentCore           -
9.1s     Lambda extracts SQL + metrics Lambda              ~200ms
9.3s     Response → API GW → User      Infrastructure      ~50ms
```

### 2.2 Repeated query in same session (STM ~3-5s)

```
Time     Step                          Who decides         Duration
──────   ────                          ───────────         ────────
  0ms    User repeats question         Frontend            -
150ms    Lambda → AgentCore Runtime    invoke_agent_runtime ~150ms
300ms    AgentCore loads STM Memory    AgentCore           ~200ms
         (includes previous Q&A)
500ms    Claude sees in memory:        Claude Sonnet 4     ~2,500ms
         "I already answered this"
         Responds from context
         WITHOUT calling tools
3.0s     Direct response               AgentCore → Lambda  ~100ms
```

### 2.3 New query in same session (cached schema ~6-8s)

```
Time     Step                          Who decides         Duration
──────   ────                          ───────────         ────────
  0ms    User asks something new       Frontend            -
150ms    Lambda → AgentCore Runtime    invoke_agent_runtime ~150ms
300ms    AgentCore loads STM Memory    AgentCore           ~200ms
         (includes schema from before)
500ms    Claude sees schema in memory  Claude Sonnet 4     ~2,500ms
         Does NOT call discover_schema()
         Generates SQL directly
3.0s     Tool call: execute_query()    Claude → Athena     ~700ms
3.7s     Claude formats response       Claude Sonnet 4     ~2,000ms
5.7s     Response → User               Infrastructure      ~100ms
```

---

## 3. Semantic Layer: From Data Lake to Data Lakehouse

### 3.1 The Semantic Layer in AWS

The semantic layer transforms a Data Lake (raw storage in S3) into a functional Data Lakehouse, combining S3 flexibility with Data Warehouse structure.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    AWS SEMANTIC LAYER — 3 PILLARS                         │
│                                                                          │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐ │
│  │  AWS Glue Data     │  │  AWS Lake           │  │  Amazon Redshift   │ │
│  │  Catalog           │  │  Formation          │  │  Spectrum          │ │
│  │                    │  │                     │  │                    │ │
│  │  Centralized       │  │  Governance and     │  │  High-performance  │ │
│  │  metastore.        │  │  security.          │  │  semantic layer    │ │
│  │  Defines schema    │  │  Semantic           │  │  over S3.          │ │
│  │  for raw data      │  │  permissions:       │  │  Query without     │ │
│  │  in S3 (crawlers)  │  │  who sees what      │  │  importing data.   │ │
│  │  to make it        │  │  data from the      │  │                    │ │
│  │  queryable.        │  │  catalog.           │  │                    │ │
│  │                    │  │                     │  │                    │ │
│  │  ✅ INCLUDED       │  │  ✅ INCLUDED        │  │  ❌ NOT NEEDED     │ │
│  └────────────────────┘  └────────────────────┘  └────────────────────┘ │
│                                                                          │
│  We use Athena as the serverless SQL engine instead of Redshift          │
│  Spectrum, since it requires no cluster and costs $0 at low volumes.     │
└──────────────────────────────────────────────────────────────────────────┘
```

### 3.2 How Claude Interprets Semantics

Claude Sonnet 4 adds a semantic inference layer that doesn't exist in traditional BI tools:

- **System prompt** (from `config/system_prompt.yaml`): business dictionary, naming conventions, few-shot examples
- **Column name inference**: `total_amount` → money, use SUM/AVG; `category` → use GROUP BY; `sale_date` → temporal, use date_parse
- This differentiates the solution from traditional BI: users ask in natural language and Claude translates intent to correct SQL

---

## 4. Security Layers

```
┌─ LAYER 1: Amazon Bedrock Guardrails ─────────────────┐
│  ✓ Blocks politics, religion, violence, sexual, hate  │
│  ✓ Blocks prompt injection                            │
│  ✗ Evaluates BEFORE Claude processes                  │
└───────────────────────────────────────────────────────┘
                         ↓
┌─ LAYER 2: System Prompt ─────────────────────────────┐
│  ✓ SELECT only                                        │
│  ✓ Always include LIMIT                               │
│  ✓ Use exact schema names                             │
└───────────────────────────────────────────────────────┘
                         ↓
┌─ LAYER 3: PolicyValidator + execute_query() ─────────┐
│  ✓ Rejects DROP, DELETE, INSERT, UPDATE, ALTER, CREATE│
│  ✓ Only executes if starts with SELECT                │
│  ✓ Athena validates SQL syntax                        │
└───────────────────────────────────────────────────────┘
                         ↓
┌─ LAYER 4: Lake Formation (permissions) ──────────────┐
│  ✓ Agent role only has SELECT on specific tables      │
│  ✓ Cannot access other databases                      │
│  ✓ Cannot create/modify tables                        │
└───────────────────────────────────────────────────────┘
```

---

## 5. Scaling for Production

### 5.1 Production scenario (~50 tables, ~500 users)

```
CURRENT (POC)                         PRODUCTION
────────────                          ──────────
3 tables                              50+ tables
~6K records                           Millions of records
1 concurrent user                     50-100 concurrent
No authentication                     Cognito + API Keys
No response cache                     ElastiCache/DynamoDB cache
Athena on-demand                      Athena provisioned capacity
Claude Sonnet 4                       Sonnet 4 (complex) + Haiku (simple)
```

### 5.2 Key production improvements

| Improvement | Current Latency | Target | How |
|-------------|----------------|--------|-----|
| DynamoDB cache | 12-15s (first) | <500ms (cache hit) | Hash query → cached response with TTL |
| Provisioned Throughput | Variable | Consistent | Reserve Claude capacity in Amazon Bedrock |
| Athena provisioned | ~700ms | ~200ms | Reserved capacity for frequent queries |
| Pre-loaded schema | 600ms (Glue call) | 0ms | Inject schema in system prompt |
| Model routing | 12-15s always | 3-5s (simple) | Haiku for COUNT/simple, Sonnet for JOINs |

### 5.3 Suggested roadmap

```
PHASE 1 (2-3 weeks): Minimum production
────────────────────────────────────────
✓ Connect to real data (existing or new Glue Catalog)
✓ Enrich schema with semantic comments
✓ Add 15-20 few-shot examples for business queries
✓ Cognito for authentication
✓ DynamoDB cache for frequent queries
✓ Estimate: ~$50-100/mo

PHASE 2 (2-3 weeks): Optimization
────────────────────────────────────
✓ Model routing (Haiku for simple, Sonnet for complex)
✓ Pre-loaded schema in prompt (eliminate discover_schema)
✓ Athena provisioned capacity
✓ CloudWatch observability dashboard
✓ Estimate: ~$150-300/mo (depending on volume)

PHASE 3 (3-4 weeks): Enterprise
────────────────────────────────────
✓ Multi-tenancy with Lake Formation
✓ Row-level security by department
✓ Feedback loop (user marks incorrect responses)
✓ Alerts and SLAs
✓ VPC + PrivateLink
✓ Estimate: ~$300-800/mo (depending on users and volume)
```

---

## 6. Comparison with Alternatives

| Criteria | This solution (AgentCore) | Amazon Q Business | Tableau Ask Data | Power BI Copilot |
|----------|--------------------------|-------------------|------------------|------------------|
| Setup | ~2 days | ~1-2 weeks | Requires Tableau | Requires Power BI |
| Customization | Full (Python code) | Limited | Limited | Limited |
| LLM Model | Claude Sonnet 4 (choice) | AWS proprietary | Salesforce proprietary | GPT-4 (Microsoft) |
| Base cost | ~$20/mo | ~$25/user/mo | ~$75/user/mo | ~$30/user/mo |
| Native Data Lake | Yes (Glue + Athena + S3) | Yes | No (needs connector) | No (needs connector) |
| Guardrails | Amazon Bedrock Guardrails | Basic | No | Basic |
| Conversational memory | STM + LTM (AgentCore) | Yes | No | Limited |

---

## 7. How to Customize This Template

### Step 1: Define your tables
Edit `config/tables.yaml` with your real data structure.

### Step 2: Configure the prompt
Edit `config/system_prompt.yaml`:
- `business_dictionary`: your business terms
- `examples`: 10-15 relevant SQL queries
- `naming_conventions`: your tables and relationships

### Step 3: Generate test data
```bash
python scripts/init_demo_data.py
aws s3 cp data/demo/ s3://YOUR-BUCKET/data/ --recursive
```

### Step 4: Deploy
```bash
cd cdk/
pip install -r requirements.txt
cdk bootstrap aws://YOUR_ACCOUNT/us-east-1
cdk deploy --all
```

### Step 5: Configure AgentCore
```bash
npm install -g @aws/agentcore-cli
agentcore init
agentcore deploy --region us-east-1
```
