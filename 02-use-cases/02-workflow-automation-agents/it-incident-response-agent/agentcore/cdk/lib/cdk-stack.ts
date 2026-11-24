import {
  AgentCoreApplication,
  type AgentCoreApplicationProps,
  type AgentCoreProjectSpec,
  transformAgentCoreJson,
} from '@aws/agentcore-cdk';
import * as cdk from 'aws-cdk-lib';
import { CfnOutput, Stack, type StackProps } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda_ from 'aws-cdk-lib/aws-lambda';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import * as path from 'path';
import * as fs from 'fs';
import { createHash } from 'crypto';
import { InfraConstruct } from './infra-construct';

export interface AgentCoreStackProps extends StackProps {
  /**
   * The AgentCore project specification containing agents, memories, and credentials.
   */
  spec: AgentCoreProjectSpec;
  /** Absolute sample root for container, Lambda and tool-schema assets. */
  projectRoot: string;
  targetName: string;
  /** Credential provider ARNs populated by CLI pre-deploy identity setup. */
  credentials?: AgentCoreApplicationProps['credentials'];
  /**
   * Pre-created Bedrock Knowledge Base ID (optional).
   */
  kbId?: string;
  /**
   * Whether to destroy data on stack delete (default: true).
   */
  destroyOnDelete?: boolean;
}

/**
 * CDK Stack that deploys both AgentCore resources AND supplementary infrastructure.
 *
 * The stack integrates:
 * 1. InfraConstruct — DynamoDB, S3, Lambda tools, SNS trigger, observability
 * 2. AgentCoreApplication — Runtime, Memory, Gateway, policy and evaluation
 * 3. Lambda target ARNs patched into the project spec before creating the application
 *
 * This enables single-command deployment via `agentcore deploy`.
 */
export class AgentCoreStack extends Stack {
  /** The AgentCore application containing all agent environments */
  public readonly application: AgentCoreApplication;
  /** The supplementary infrastructure construct */
  public readonly infra: InfraConstruct;
  /** Jira OAuth provider name (empty string if Jira not configured) */
  private readonly jiraProviderName: string;
  private jiraProviderArn?: string;
  private jiraManagedSecretArn?: string;
  private readonly projectRoot: string;
  private sdkLayer?: lambda_.LayerVersion;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const { spec, credentials, kbId, destroyOnDelete, projectRoot, targetName } = props;
    this.projectRoot = projectRoot;

    // ─── Step 0: Jira integration (conditional) ───────────────────
    // References an external secret/version and creates an owned AtlassianOauth2
    // provider only when JIRA_OAUTH_CLIENT_ID is set.
    this.jiraProviderName = '';
    const jiraClientId = process.env.JIRA_OAUTH_CLIENT_ID || '';
    if (jiraClientId) {
      if (!process.env.JIRA_RUNTIME_USER_ID || !/^[A-Za-z0-9_-]{1,128}$/.test(process.env.JIRA_RUNTIME_USER_ID)) {
        throw new Error('JIRA_RUNTIME_USER_ID must identify the approved synthetic federation user');
      }
      if (!/^[a-fA-F0-9-]{36}$/.test(process.env.JIRA_CLOUD_ID || '') ||
          !/^https:\/\/[a-zA-Z0-9-]+\.atlassian\.net$/.test(process.env.JIRA_SITE_URL || '')) {
        throw new Error('Jira requires an explicit approved JIRA_CLOUD_ID and https Atlassian site');
      }
      const jiraTools = (process.env.JIRA_ALLOWED_TOOLS || '').split(/\s+/).filter(Boolean);
      if (jiraTools.length !== 3 || new Set(jiraTools).size !== 3 || jiraTools.some(t => !/^jira_[A-Za-z0-9_-]+$/.test(t))) {
        throw new Error('JIRA_ALLOWED_TOOLS must name the three approved issue read/comment/transition tools');
      }
      this.jiraProviderName = this.createJiraOauthProvider(jiraClientId);
    }

    // ─── Step 1: Deploy supplementary infrastructure ───────────────
    // Creates DynamoDB tables, S3 buckets, Lambda tool functions, SNS trigger.
    // The lambdaArnMap maps gateway target names to their real Lambda ARNs.
    this.infra = new InfraConstruct(this, 'Infra', {
      projectRoot,
      kbId,
      destroyOnDelete: destroyOnDelete ?? true,
      skipKb: process.env.SKIP_KB === 'true',
    });

    // v1 owns gateways inside AgentCoreApplication. Patch BEFORE construction:
    // a separate AgentCoreMcp would duplicate gateways and lose policy bindings.
    const patchedSpec = this.patchLambdaArns(spec, this.infra.lambdaArnMap);
    this.application = new AgentCoreApplication(this, 'Application', {
      ...transformAgentCoreJson(patchedSpec, { projectRoot, credentials }),
      targetName,
    });

    if (patchedSpec.agentCoreGateways.length > 0) {

      // ─── Step 5.1: Order GatewayTargets AFTER the gateway role's invoke policy ──
      // ROOT CAUSE: AgentCore validates that the Gateway execution ROLE has an
      // IDENTITY-BASED lambda:InvokeFunction permission at GatewayTarget creation
      // time. The AgentCoreMcp L3 construct DOES create that policy
      // (Gateway Role DefaultPolicy), but it does NOT make the GatewayTarget
      // resources depend on it. Without an explicit DependsOn, CloudFormation may
      // create the GatewayTargets before the role policy is attached, so the
      // service rejects them with:
      //   "Gateway execution role lacks permission to invoke Lambda function ..."
      // A resource-based Lambda permission (fn.addPermission) does NOT satisfy this
      // check — AgentCore inspects the role's identity policy, not the Lambda's
      // resource policy. The fix is a hard CloudFormation ordering dependency:
      // every GatewayTarget must depend on the gateway role's DefaultPolicy.
      const gatewayRolePolicy = this.node
        .findAll()
        .find(
          c =>
            (c as cdk.CfnResource).cfnResourceType === 'AWS::IAM::Policy' &&
            c.node.path.includes('Gateway') &&
            c.node.path.includes('Role') &&
            c.node.path.includes('DefaultPolicy')
        ) as cdk.CfnResource | undefined;

      if (gatewayRolePolicy) {
        const gatewayTargets = this.node
          .findAll()
          .filter(
            c => (c as cdk.CfnResource).cfnResourceType === 'AWS::BedrockAgentCore::GatewayTarget'
          ) as cdk.CfnResource[];
        for (const target of gatewayTargets) {
          target.addDependency(gatewayRolePolicy);
        }
      }

      // ─── Step 5a: Policy Engine ──────────────────────────────────────
      // STEP: POLICY — Defined declaratively in agentcore.json → policyEngines[]
      // with policyEngineConfiguration on the gateway. The L3 construct renders
      // PolicyEngine + Policy resources to CloudFormation automatically.
    }

    // ─── Step 5: Inject custom env vars into the Runtime ─────────────
    // The L3 construct manages standard env vars using its own naming convention:
    //   AGENTCORE_GATEWAY_{NAME}_URL, MEMORY_{NAME}_ID, etc.
    // We add a GATEWAY_URL alias and additional env vars for features the L3
    // doesn't know about (guardrails, EventBridge, DynamoDB, model routing).
    const runtimeConstruct = this.application.node
      .findAll()
      .find(c => (c as any).cfnResourceType === 'AWS::BedrockAgentCore::Runtime');
    if (runtimeConstruct) {
      const cfnRuntime = runtimeConstruct as cdk.CfnResource;

      // GATEWAY_URL alias: The L3 sets AGENTCORE_GATEWAY_ITINCIDENTGATEWAY_URL
      // but agent code also reads GATEWAY_URL for backward compatibility.
      // Find the gateway resource to get its URL attribute.
      const gatewayCfn = this.node
        .findAll()
        .find(c => (c as any).cfnResourceType === 'AWS::BedrockAgentCore::Gateway') as cdk.CfnResource | undefined;
      if (gatewayCfn) {
        cfnRuntime.addPropertyOverride('EnvironmentVariables.GATEWAY_URL', gatewayCfn.getAtt('GatewayUrl'));
      }

      cfnRuntime.addPropertyOverride('EnvironmentVariables.OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT',
        process.env.IT_SAMPLE_EVALUATION_CONTENT === 'synthetic-only' ? 'true' : 'false');
      cfnRuntime.addPropertyOverride('EnvironmentVariables.GUARDRAIL_ID', this.infra.guardrailId);
      cfnRuntime.addPropertyOverride(
        'EnvironmentVariables.GUARDRAIL_VERSION',
        process.env.GUARDRAIL_VERSION || 'DRAFT'
      );
      cfnRuntime.addPropertyOverride('EnvironmentVariables.EVENT_BUS_NAME', this.infra.eventBusName);
      cfnRuntime.addPropertyOverride('EnvironmentVariables.TICKETS_TABLE', this.infra.ticketsTable.tableName);
      // NOTE: AGENT_MODEL_ID / FAST_MODEL_ID are now defined declaratively in
      // agentcore.json → runtimes[].envVars[] (static config, not ARN-dependent).
      // Only genuinely ARN/resource-derived env vars remain as imperative overrides here.

      // Explicit deploy-time overrides take precedence over committed runtime config.
      const runtimeEnv = Object.fromEntries(
        spec.runtimes.find(runtime => runtime.name === 'ITIncidentAgent')?.envVars?.map(v => [v.name, v.value]) ?? []
      );
      const authMode = process.env.GATEWAY_AUTH_MODE || runtimeEnv.GATEWAY_AUTH_MODE || 'AWS_IAM';
      const gatewayAuth = patchedSpec.agentCoreGateways.find(g => g.name === 'ITIncidentGateway')?.authorizerType;
      if (gatewayAuth && authMode !== gatewayAuth) throw new Error('Runtime and Gateway authentication modes must agree');
      cfnRuntime.addPropertyOverride('EnvironmentVariables.GATEWAY_AUTH_MODE', authMode);
      if (authMode === 'CUSTOM_JWT') {
        // New naming: GATEWAY_OAUTH_* (boundary-scoped). Falls back to legacy names.
        const oauthProvider = process.env.GATEWAY_OAUTH_PROVIDER_NAME || process.env.OAUTH_PROVIDER_NAME || runtimeEnv.GATEWAY_OAUTH_PROVIDER_NAME || 'auth0-m2m';
        const oauthAudience = process.env.GATEWAY_OAUTH_AUDIENCE || process.env.GATEWAY_AUDIENCE || runtimeEnv.GATEWAY_OAUTH_AUDIENCE || '';
        cfnRuntime.addPropertyOverride('EnvironmentVariables.GATEWAY_OAUTH_PROVIDER_NAME', oauthProvider);
        cfnRuntime.addPropertyOverride('EnvironmentVariables.GATEWAY_OAUTH_AUDIENCE', oauthAudience);
        const scopes = (process.env.GATEWAY_OAUTH_SCOPES || runtimeEnv.GATEWAY_OAUTH_SCOPES || '').split(/\s+/).filter(Boolean);
        const allowed = patchedSpec.agentCoreGateways.find(g => g.name === 'ITIncidentGateway')?.authorizerConfiguration?.customJwtAuthorizer?.allowedScopes ?? [];
        if (!allowed.length || scopes.length !== allowed.length || scopes.some(s => !allowed.includes(s))) {
          throw new Error('GATEWAY_OAUTH_SCOPES must match the explicit Gateway allowedScopes');
        }
        cfnRuntime.addPropertyOverride('EnvironmentVariables.GATEWAY_OAUTH_SCOPES', scopes.join(' '));
      }

      // ─── Jira integration (opt-in) ─────────────────────────────
      // When JIRA_OAUTH_CLIENT_ID is set, inject Jira env vars into the Runtime.
      // The agent code reads JIRA_MCP_URL to detect Jira mode.
      const jiraClientId = process.env.JIRA_OAUTH_CLIENT_ID || '';
      if (jiraClientId) {
        cfnRuntime.addPropertyOverride('EnvironmentVariables.JIRA_MCP_URL', 'https://mcp.atlassian.com/v1/sse');
        cfnRuntime.addPropertyOverride('EnvironmentVariables.JIRA_SITE_URL', process.env.JIRA_SITE_URL || '');
        cfnRuntime.addPropertyOverride('EnvironmentVariables.JIRA_CLOUD_ID', process.env.JIRA_CLOUD_ID!);
        cfnRuntime.addPropertyOverride('EnvironmentVariables.JIRA_PROJECT_KEY', process.env.JIRA_PROJECT_KEY || 'INC');
        cfnRuntime.addPropertyOverride('EnvironmentVariables.JIRA_OAUTH_PROVIDER_NAME', this.jiraProviderName || '');
        cfnRuntime.addPropertyOverride('EnvironmentVariables.JIRA_ALLOWED_TOOLS', process.env.JIRA_ALLOWED_TOOLS || '');
      }

      // ─── OBSERVABILITY: OpenTelemetry & X-Ray Configuration ────────
      // These env vars are now defined DECLARATIVELY in agentcore.json → runtimes[].envVars[]
      // plus instrumentation.enableOtel: true. The L3 construct injects them automatically.
      // No imperative addPropertyOverride needed.

      // Wire the Runtime ARN into the Trigger Lambda so it can invoke the agent
      const runtimeArn = cfnRuntime.getAtt('AgentRuntimeArn').toString();
      this.infra.triggerFn.addEnvironment('AGENT_RUNTIME_ARN', runtimeArn);
      if (jiraClientId) this.infra.triggerFn.addEnvironment('JIRA_RUNTIME_USER_ID', process.env.JIRA_RUNTIME_USER_ID!);

      // Grant the trigger Lambda permission to invoke THIS specific runtime (least privilege)
      // The InvokeAgentRuntime action requires access to both the runtime ARN and its
      // endpoint sub-resource (e.g., .../runtime-endpoint/DEFAULT), hence the /* suffix.
      this.infra.triggerFn.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ['bedrock-agentcore:InvokeAgentRuntime', ...(jiraClientId ? ['bedrock-agentcore:InvokeAgentRuntimeForUser'] : [])],
          resources: [runtimeArn, `${runtimeArn}/*`],
        })
      );
    }

    // ─── Step 5b: Grant Runtime execution role additional permissions ──
    // The L3 role includes model, memory, gateway and telemetry grants. These
    // additional sample grants are preserved; see migration notes for IAM risks.
    // Our agent code also needs:
    //   - dynamodb:UpdateItem on Tickets table (write resolution / mark failed)
    //   - bedrock:ApplyGuardrail (PII filtering)
    //   - events:PutEvents (EventBridge emission)
    //   - bedrock-agentcore:GetResourceOauth2Token (Jira 3LO token fetch)
    // NOTE: X-Ray put-trace permissions are NOT added here — the L3 RuntimeExecutionRole
    // already grants xray:PutTraceSegments / xray:PutTelemetryRecords on '*'.
    // Filter specifically for the Runtime ExecutionRole (not Memory's role).
    const runtimeRole = this.application.node
      .findAll()
      .find(
        c =>
          (c as any).cfnResourceType === 'AWS::IAM::Role' &&
          c.node.path.includes('Runtime') &&
          c.node.path.includes('ExecutionRole')
      );
    if (runtimeRole) {
      const cfnRole = runtimeRole as cdk.CfnResource;
      // Use the logical ID to get the role for policy attachment
      const roleName = cfnRole.ref;

      const statements = [
        new iam.PolicyStatement({
          sid: 'DynamoDBTicketsAccess',
          actions: ['dynamodb:UpdateItem', 'dynamodb:GetItem'],
          resources: [this.infra.ticketsTable.tableArn],
        }),
        new iam.PolicyStatement({
          sid: 'IncidentRecurrenceQuery',
          actions: ['dynamodb:Query'],
          resources: [`${this.infra.ticketsTable.tableArn}/index/byRequester`],
        }),
        new iam.PolicyStatement({
          sid: 'GuardrailAccess',
          actions: ['bedrock:ApplyGuardrail'],
          resources: [`arn:${this.partition}:bedrock:${this.region}:${this.account}:guardrail/${this.infra.guardrailId}`],
        }),
        new iam.PolicyStatement({
          sid: 'EventBridgeAccess',
          actions: ['events:PutEvents'],
          resources: [`arn:${this.partition}:events:${this.region}:${this.account}:event-bus/${this.infra.eventBusName}`],
        }),
        // The application does not query Logs Insights. Operator verification uses
        // the operator role, not extra account-wide permissions in the runtime.
      ];

      const memory = this.application.memories.get('ITIncidentAgentMemory');
      if (memory) {
        // The application uses namespacePath=incidents/<actor>, not a session leaf.
        // Do not broaden namespace writes or grant reads on another Memory.
        statements.push(new iam.PolicyStatement({
          sid: 'CrossSessionIncidentMemoryRead',
          actions: ['bedrock-agentcore:RetrieveMemoryRecords'],
          resources: [memory.memoryArn],
          conditions: { StringLike: { 'bedrock-agentcore:namespacePath': 'incidents/*' } },
        }));
      }
      const identitySecrets = Object.values(credentials ?? {}).map(c => c.clientSecretArn).filter((arn): arn is string => !!arn);
      if (this.jiraManagedSecretArn) identitySecrets.push(this.jiraManagedSecretArn);
      if (identitySecrets.length) statements.push(new iam.PolicyStatement({
        sid: 'ConfiguredIdentitySecretsOnly', actions: ['secretsmanager:GetSecretValue'], resources: identitySecrets,
      }));
      if (jiraClientId && this.jiraProviderArn) {
        statements.push(
          new iam.PolicyStatement({
            sid: 'AgentCoreIdentityJiraAccess',
            actions: ['bedrock-agentcore:GetResourceOauth2Token'],
            resources: [this.jiraProviderArn,
              `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:token-vault/default`,
              `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/*`],
          })
        );
      }

      new iam.Policy(this, 'RuntimeAdditionalPolicy', {
        policyName: 'AgentAdditionalPermissions',
        roles: [iam.Role.fromRoleName(this, 'RuntimeRoleRef', roleName)],
        statements,
      });
    }

    // ─── Step 6: Online Evaluation (declarative) ─────────────────────────
    // Online evaluation is defined declaratively in agentcore.json →
    // onlineEvalConfigs[]. The AgentCoreApplication L3 construct creates the
    // OnlineEvaluationConfig resource with proper IAM role and dependency
    // ordering automatically. No custom resource needed.
    //
    // To disable: set onlineEvalConfigs to [] in agentcore.json.
    //
    // PREREQUISITE: CloudWatch Transaction Search must be enabled at the
    // account level so that OTEL spans land in the `aws/spans` log group
    // (the data source the online eval pipeline reads from). Step 6a below
    // verifies observed readiness; shared setup remains account-owner controlled.
    if (spec.onlineEvalConfigs && spec.onlineEvalConfigs.length > 0) {
      const transactionSearch = this.enableTransactionSearch();
      for (const evaluation of this.application.onlineEvalConfigs.values()) {
        evaluation.node.addDependency(transactionSearch);
      }
    }

    // Strip unused bootstrap grants from the pinned L3 runtime policy. This
    // scenario has no configuration bundles, API keys, or runtime secret reads.
    for (const node of this.application.node.findAll()) {
      if (node instanceof iam.CfnPolicy && node.node.path.includes('Runtime')) {
        const original = node.policyDocument;
        node.policyDocument = cdk.Lazy.any({ produce: () => {
          const document = this.resolve(original);
          document.Statement = document.Statement.map((statement: any) => {
            const actions = (Array.isArray(statement.Action) ? statement.Action : [statement.Action]) as string[];
            const kept = actions.filter(action => !action.includes('ConfigurationBundle') &&
              !['secretsmanager:GetSecretValue', 'logs:PutResourcePolicy', 'logs:GetLogEvents', 'logs:FilterLogEvents', 'bedrock-agentcore:CreateWorkloadIdentity',
                'bedrock-agentcore:GetApiKeyCredential', 'bedrock-agentcore:GetResourceApiKey'].includes(action));
            if (!kept.length) return undefined;
            // Bound the L3 OAuth bootstrap to this account/region, never all regions.
            const result = { ...statement, Action: kept };
            if (kept.includes('bedrock-agentcore:GetResourceOauth2Token')) {
              const providers = Object.values(credentials ?? {}).map(c => c.credentialProviderArn);
              result.Resource = [...providers,
                `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:token-vault/default`,
                `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/*`];
            }
            return result;
          }).filter(Boolean);
          return document;
        } });
      }
    }

    // ─── Step 7: Stack outputs ─────────────────────────────────────
    new CfnOutput(this, 'StackNameOutput', {
      description: 'Name of the CloudFormation Stack',
      value: this.stackName,
    });
  }

  /**
   * STEP: OBSERVABILITY — Verify CloudWatch Transaction Search via custom resource.
   *
   * Default behavior is read-only, checking ACTIVE routing and actual indexing.
   * Explicit account-owner approval is required to grant/configure X-Ray writes.
   * Logs delivery policy preparation is a separate account-owner prerequisite.
   *
   * This is an account- and region-level setting. On stack delete it is intentionally
   * left enabled (other agents in the account may depend on it).
   */
  private infrastructureSdk(): lambda_.LayerVersion {
    if (this.sdkLayer) return this.sdkLayer;
    const directory = path.join(this.projectRoot, '.lambda-sdk');
    const manifestFile = path.join(directory, 'manifest.json');
    if (!fs.existsSync(manifestFile)) throw new Error('Run python scripts/package_sdk.py before synthesis');
    const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
    const hash = (file: string) => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
    if (manifest.lock_sha256 !== hash(path.join(this.projectRoot, 'lambdas/sdk-requirements.txt'))) {
      throw new Error('Infrastructure SDK layer does not match its lock');
    }
    for (const [relative, expected] of Object.entries(manifest.files)) {
      if (path.isAbsolute(relative) || relative.split('/').includes('..') ||
          hash(path.join(directory, relative)) !== expected) throw new Error('Infrastructure SDK artifact mismatch');
    }
    this.sdkLayer = new lambda_.LayerVersion(this, 'InfrastructureSdk', {
      code: lambda_.Code.fromAsset(directory),
      compatibleRuntimes: [lambda_.Runtime.PYTHON_3_11, lambda_.Runtime.PYTHON_3_12],
      description: 'Locked boto3/botocore 1.43.22 for custom resource SDK contracts',
    });
    return this.sdkLayer;
  }

  private enableTransactionSearch(): cdk.CustomResource {
    const projectRoot = this.projectRoot;

    const txnSearchFn = new lambda_.Function(this, 'TransactionSearchFn', {
      runtime: lambda_.Runtime.PYTHON_3_11,
      handler: 'infra.transaction_search.handler',
      layers: [this.infrastructureSdk()],
      timeout: cdk.Duration.minutes(2),
      memorySize: 256,
      code: lambda_.Code.fromAsset(path.join(projectRoot, 'lambdas')),
    });

    const allowMutation = process.env.TXN_SEARCH_ALLOW_MUTATION === 'true';
    const approval = process.env.TXN_SEARCH_ACCOUNT_OWNER_APPROVAL || '';
    if (allowMutation && approval !== `${this.account}/${this.region}`) {
      throw new Error('Transaction Search mutation requires exact account-owner approval');
    }
    // Read-only by default; Logs delivery policy is an account-owner prerequisite.
    txnSearchFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'TransactionSearchConfig',
        actions: [
          'xray:GetTraceSegmentDestination', 'xray:GetIndexingRules',
          ...(allowMutation ? ['xray:UpdateTraceSegmentDestination', 'xray:UpdateIndexingRule'] : []),
        ],
        resources: ['*'],
      })
    );

    const provider = new cr.Provider(this, 'TransactionSearchProvider', {
      onEventHandler: txnSearchFn,
    });

    const resource = new cdk.CustomResource(this, 'EnableTransactionSearch', {
      serviceToken: provider.serviceToken,
      properties: {
        IndexingPercentage: process.env.TXN_SEARCH_INDEXING_PERCENTAGE || '100',
        Account: this.account, Region: this.region,
        AllowMutation: String(allowMutation), AccountOwnerApproval: approval,
        Version: '2',
      },
    });

    new CfnOutput(this, 'TransactionSearchEnabled', {
      description: 'CloudWatch Transaction Search routes X-Ray spans to the aws/spans log group',
      value: resource.getAttString('Status'),
    });
    return resource;
  }

  /**
   * Replace placeholder Lambda ARNs in the MCP spec with real function ARNs.
   *
   * Iterates through gateway targets of type `lambdaFunctionArn` and replaces
   * any ARN containing "PLACEHOLDER" with the real ARN from the lambdaArnMap.
   * Only query-kb may be omitted, when the KB is explicitly disabled. Unknown
   * placeholders fail synthesis rather than silently removing tools.
   */
  private patchLambdaArns(spec: AgentCoreProjectSpec, lambdaArnMap: Record<string, string>): AgentCoreProjectSpec {
    const patched: AgentCoreProjectSpec = structuredClone(spec);

    for (const gateway of patched.agentCoreGateways) {
      gateway.targets = gateway.targets.filter(target => {
        if (target.targetType !== 'lambdaFunctionArn' || !target.lambdaFunctionArn) return true;
        if (!target.lambdaFunctionArn.lambdaArn.startsWith('PLACEHOLDER_')) return true;
        const expected = `PLACEHOLDER_${target.name.toUpperCase().replace(/-/g, '_')}`;
        if (target.lambdaFunctionArn.lambdaArn !== expected) throw new Error(`Unexpected placeholder for ${target.name}`);
        const realArn = lambdaArnMap[target.name];
        if (realArn) {
          target.lambdaFunctionArn.lambdaArn = realArn;
          return true;
        }
        if (target.name === 'query-kb' && !this.infra.knowledgeBaseId) return false;
        throw new Error(`No Lambda ARN for gateway target "${target.name}"`);
      });
    }

    return patched;
  }

  /**
   * STEP: IDENTITY — Create Atlassian OAuth2 credential provider (conditional).
   *
   * References an external Secrets Manager secret/version and registers an owned
   * AtlassianOauth2 provider with AgentCore Identity. Values are never read at synth.
   * The agent uses @requires_access_token(auth_flow="USER_FEDERATION") at
   * runtime to obtain Jira access tokens — it never sees the secret.
   *
   * Only called when JIRA_OAUTH_CLIENT_ID is set at deploy time.
   */
  private createJiraOauthProvider(clientId: string): string {
    const projectRoot = this.projectRoot;
    const secretArn = process.env.JIRA_OAUTH_SECRET_ARN || '';
    const secretVersionId = process.env.JIRA_OAUTH_SECRET_VERSION_ID || '';
    const providerName = `${this.stackName.toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 80)}_jira_3lo`;
    const partition = this.region.startsWith('us-gov-') ? 'aws-us-gov' : this.region.startsWith('cn-') ? 'aws-cn' : 'aws';

    if (process.env.JIRA_OAUTH_CLIENT_SECRET ||
        !secretArn.startsWith(`arn:${partition}:secretsmanager:${this.region}:${this.account}:secret:`) ||
        !/^[a-zA-Z0-9-]{32,64}$/.test(secretVersionId)) {
      throw new Error('Jira requires an external same-account/region JIRA_OAUTH_SECRET_ARN and immutable JIRA_OAUTH_SECRET_VERSION_ID; never a plaintext client secret');
    }
    const version = new cdk.CfnParameter(this, 'JiraSecretVersionId', {
      type: 'String', default: secretVersionId, allowedPattern: '[a-zA-Z0-9-]{32,64}',
      description: 'External Jira secret VersionId; update this parameter for secret-only rotation',
    });
    new CfnOutput(this, 'JiraExternalSecretArn', { value: secretArn });
    const ownership = new dynamodb.Table(this, 'JiraProviderOwnership', {
      partitionKey: { name: 'owner', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
    });
    new CfnOutput(this, 'JiraOwnershipTable', { value: ownership.tableName });

    // Custom resource Lambda for OAuth provider lifecycle
    const providerFn = new lambda_.Function(this, 'JiraOauthProviderFn', {
      runtime: lambda_.Runtime.PYTHON_3_11,
      handler: 'infra.jira_oauth_provider.handler',
      layers: [this.infrastructureSdk()],
      timeout: cdk.Duration.minutes(3),
      memorySize: 256,
      code: lambda_.Code.fromAsset(path.join(projectRoot, 'lambdas')),
    });
    providerFn.addEnvironment('OWNERSHIP_TABLE', ownership.tableName);
    providerFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem'], resources: [ownership.tableArn],
    }));
    providerFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'], resources: [secretArn],
    }));
    // If the external secret uses a customer CMK, grant decrypt only on that key.
    if (process.env.JIRA_OAUTH_SECRET_KMS_KEY_ARN) {
      providerFn.addToRolePolicy(new iam.PolicyStatement({ actions: ['kms:Decrypt'],
        resources: [process.env.JIRA_OAUTH_SECRET_KMS_KEY_ARN],
        conditions: { StringEquals: { 'kms:ViaService': `secretsmanager.${this.region}.amazonaws.com`,
          'kms:EncryptionContext:SecretARN': secretArn } },
      }));
    }
    providerFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'bedrock-agentcore:CreateOauth2CredentialProvider',
          'bedrock-agentcore:UpdateOauth2CredentialProvider',
          'bedrock-agentcore:DeleteOauth2CredentialProvider',
          'bedrock-agentcore:GetOauth2CredentialProvider',
        ],
        resources: [`arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:token-vault/default`,
          `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:token-vault/default/oauth2credentialprovider/${providerName}-*`],
      })
    );

    // CDK Provider framework (prevents 1-hour hangs on Lambda failure)
    const provider = new cr.Provider(this, 'JiraOauthProvider', {
      onEventHandler: providerFn,
    });

    const jiraCr = new cdk.CustomResource(this, 'JiraOauthProviderCR', {
      serviceToken: provider.serviceToken,
      properties: {
        ProviderName: providerName,
        Vendor: 'AtlassianOauth2',
        ClientId: clientId,
        SecretArn: secretArn,
        SecretVersionId: version.valueAsString,
      },
    });

    // Outputs for post-deploy setup (callback URL registration)
    this.jiraProviderArn = jiraCr.getAttString('ProviderArn');
    this.jiraManagedSecretArn = jiraCr.getAttString('ClientSecretArn');
    new CfnOutput(this, 'JiraOauthProviderArn', { value: this.jiraProviderArn });
    new CfnOutput(this, 'JiraOauthProviderName', { value: jiraCr.getAttString('ProviderName') });
    new CfnOutput(this, 'JiraOauthCallbackUrl', {
      value: jiraCr.getAttString('CallbackUrl'),
      description: 'Add this URL to the Atlassian OAuth app allowed callback URLs',
    });

    return jiraCr.getAttString('ProviderName');
  }
}
