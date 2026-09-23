import {
  AgentCoreApplication,
  AgentCoreMcp,
  type AgentCoreProjectSpec,
  type AgentCoreMcpSpec,
  ContainerSourceAssetFromPath,
  AgentEcrRepository,
  ContainerBuildProject,
  ContainerImageBuilder,
} from '@aws/agentcore-cdk';
import * as cdk from 'aws-cdk-lib';
import { CfnOutput, Stack, type StackProps } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda_ from 'aws-cdk-lib/aws-lambda';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import * as path from 'path';
import { InfraConstruct } from './infra-construct';

export interface HarnessConfig {
  name: string;
  executionRoleArn?: string;
  memoryName?: string;
  containerUri?: string;
  hasDockerfile?: boolean;
  dockerfileName?: string;
  harnessDir?: string;
  tools?: { type: string; name: string }[];
  apiKeyArn?: string;
}

export interface AgentCoreStackProps extends StackProps {
  /**
   * The AgentCore project specification containing agents, memories, and credentials.
   */
  spec: AgentCoreProjectSpec;
  /**
   * The MCP specification containing gateways and servers.
   */
  mcpSpec?: AgentCoreMcpSpec;
  /**
   * Credential provider ARNs from deployed state, keyed by credential name.
   */
  credentials?: Record<string, { credentialProviderArn: string; clientSecretArn?: string }>;
  /**
   * Harness role configurations. Each entry creates an IAM execution role for a harness.
   */
  harnesses?: HarnessConfig[];
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
 * 2. AgentCoreApplication — Runtime, Memory (from @aws/agentcore-cdk)
 * 3. AgentCoreMcp — Gateway + targets with real Lambda ARNs from step 1
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

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const { spec, mcpSpec, credentials, harnesses, kbId, destroyOnDelete } = props;

    // ─── Step 0: Jira integration (conditional) ───────────────────
    // Creates Secrets Manager secret + AtlassianOauth2 credential provider
    // only when JIRA_OAUTH_CLIENT_ID is set in the environment.
    this.jiraProviderName = '';
    const jiraClientId = process.env.JIRA_OAUTH_CLIENT_ID || '';
    if (jiraClientId) {
      this.jiraProviderName = this.createJiraOauthProvider(jiraClientId);
    }

    // ─── Step 1: Deploy supplementary infrastructure ───────────────
    // Creates DynamoDB tables, S3 buckets, Lambda tool functions, SNS trigger.
    // The lambdaArnMap maps gateway target names to their real Lambda ARNs.
    this.infra = new InfraConstruct(this, 'Infra', {
      kbId,
      destroyOnDelete: destroyOnDelete ?? true,
      skipKb: process.env.SKIP_KB === 'true',
    });

    // ─── Step 2: Patch mcpSpec with real Lambda ARNs ───────────────
    // The agentcore.json has placeholder ARNs. Replace them with the real
    // function ARNs created by InfraConstruct so the gateway targets point
    // to the actual Lambda functions.
    const patchedMcpSpec = mcpSpec ? this.patchMcpSpecArns(mcpSpec, this.infra.lambdaArnMap) : undefined;

    // ─── Step 3: Build container images for harnesses ──────────────
    const harnessesForCdk = harnesses ? [...harnesses] : [];
    if (harnesses) {
      for (let i = 0; i < harnesses.length; i++) {
        const h = harnesses[i]!;
        if (h.hasDockerfile && !h.containerUri && h.harnessDir) {
          const pascalName = h.name.replace(/(^|_)([a-z])/g, (_: string, __: string, c: string) => c.toUpperCase());
          const sourceAsset = new ContainerSourceAssetFromPath(this, `Harness${pascalName}SourceAsset`, {
            sourcePath: h.harnessDir,
          });
          const ecrRepo = new AgentEcrRepository(this, `Harness${pascalName}EcrRepo`, {
            projectName: spec.name,
            agentName: `harness-${h.name}`,
          });
          const buildProject = ContainerBuildProject.getOrCreate(this);
          buildProject.grantPushTo(ecrRepo.repository);
          sourceAsset.asset.grantRead(buildProject.role);

          const builder = new ContainerImageBuilder(this, `Harness${pascalName}ContainerBuild`, {
            buildProject,
            sourceAsset,
            repository: ecrRepo,
            dockerfile: h.dockerfileName ?? 'Dockerfile',
          });

          new CfnOutput(this, `Harness${pascalName}ContainerUriOutput`, {
            value: builder.containerUri,
          });

          harnessesForCdk[i] = { ...h, containerUri: builder.containerUri };
        }
      }
    }

    // ─── Step 4: Create AgentCore Application ──────────────────────
    // Deploys Runtime + Memory + PolicyEngine + OnlineEval using @aws/agentcore-cdk L3 constructs.
    // All resources declared in agentcore.json are rendered to CloudFormation by the L3 construct.
    this.application = new AgentCoreApplication(this, 'Application', {
      spec,
      harnesses: harnessesForCdk.length > 0 ? harnessesForCdk : undefined,
    });

    // ─── Step 5: Create AgentCore MCP (Gateway + Targets) ──────────
    if (patchedMcpSpec?.agentCoreGateways && patchedMcpSpec.agentCoreGateways.length > 0) {
      new AgentCoreMcp(this, 'Mcp', {
        projectName: spec.name,
        mcpSpec: patchedMcpSpec,
        agentCoreApplication: this.application,
        credentials,
        projectTags: spec.tags,
      });

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

      // Auth mode: read from env or default to AWS_IAM
      const authMode = process.env.GATEWAY_AUTH_MODE || 'AWS_IAM';
      cfnRuntime.addPropertyOverride('EnvironmentVariables.GATEWAY_AUTH_MODE', authMode);
      if (authMode === 'CUSTOM_JWT') {
        // New naming: GATEWAY_OAUTH_* (boundary-scoped). Falls back to legacy names.
        const oauthProvider = process.env.GATEWAY_OAUTH_PROVIDER_NAME || process.env.OAUTH_PROVIDER_NAME || 'auth0-m2m';
        const oauthAudience = process.env.GATEWAY_OAUTH_AUDIENCE || process.env.GATEWAY_AUDIENCE || '';
        cfnRuntime.addPropertyOverride('EnvironmentVariables.GATEWAY_OAUTH_PROVIDER_NAME', oauthProvider);
        cfnRuntime.addPropertyOverride('EnvironmentVariables.GATEWAY_OAUTH_AUDIENCE', oauthAudience);
      }

      // ─── Jira integration (opt-in) ─────────────────────────────
      // When JIRA_OAUTH_CLIENT_ID is set, inject Jira env vars into the Runtime.
      // The agent code reads JIRA_MCP_URL to detect Jira mode.
      const jiraClientId = process.env.JIRA_OAUTH_CLIENT_ID || '';
      if (jiraClientId) {
        cfnRuntime.addPropertyOverride('EnvironmentVariables.JIRA_MCP_URL', 'https://mcp.atlassian.com/v1/sse');
        cfnRuntime.addPropertyOverride('EnvironmentVariables.JIRA_SITE_URL', process.env.JIRA_SITE_URL || '');
        cfnRuntime.addPropertyOverride('EnvironmentVariables.JIRA_PROJECT_KEY', process.env.JIRA_PROJECT_KEY || 'INC');
        cfnRuntime.addPropertyOverride('EnvironmentVariables.JIRA_OAUTH_PROVIDER_NAME', this.jiraProviderName || '');
      }

      // ─── OBSERVABILITY: OpenTelemetry & X-Ray Configuration ────────
      // These env vars are now defined DECLARATIVELY in agentcore.json → runtimes[].envVars[]
      // plus instrumentation.enableOtel: true. The L3 construct injects them automatically.
      // No imperative addPropertyOverride needed.

      // Wire the Runtime ARN into the Trigger Lambda so it can invoke the agent
      const runtimeArn = cfnRuntime.getAtt('AgentRuntimeArn').toString();
      this.infra.triggerFn.addEnvironment('AGENT_RUNTIME_ARN', runtimeArn);

      // Grant the trigger Lambda permission to invoke THIS specific runtime (least privilege)
      // The InvokeAgentRuntime action requires access to both the runtime ARN and its
      // endpoint sub-resource (e.g., .../runtime-endpoint/DEFAULT), hence the /* suffix.
      this.infra.triggerFn.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ['bedrock-agentcore:InvokeAgentRuntime'],
          resources: [runtimeArn, `${runtimeArn}/*`],
        })
      );
    }

    // ─── Step 5b: Grant Runtime execution role additional permissions ──
    // The L3 construct's execution role only has bedrock:InvokeModel and
    // memory permissions by default. Our agent code also needs:
    //   - dynamodb:UpdateItem on Tickets table (write resolution / mark failed)
    //   - bedrock:ApplyGuardrail (PII filtering)
    //   - events:PutEvents (EventBridge emission)
    //   - bedrock-agentcore:GetResourceOauth2Token (Jira 3LO token fetch)
    //   - logs:StartQuery, logs:GetQueryResults, logs:StopQuery, etc. (CloudWatch Logs Insights)
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
          actions: ['dynamodb:UpdateItem', 'dynamodb:GetItem', 'dynamodb:PutItem'],
          resources: [this.infra.ticketsTable.tableArn],
        }),
        new iam.PolicyStatement({
          sid: 'GuardrailAccess',
          actions: ['bedrock:ApplyGuardrail'],
          resources: [`arn:aws:bedrock:${this.region}:${this.account}:guardrail/*`],
        }),
        new iam.PolicyStatement({
          sid: 'EventBridgeAccess',
          actions: ['events:PutEvents'],
          resources: [`arn:aws:events:${this.region}:${this.account}:event-bus/${this.infra.eventBusName}`],
        }),
        // ─── OBSERVABILITY: CloudWatch Logs Insights ──
        // NOTE: X-Ray put-trace permissions (xray:PutTraceSegments / PutTelemetryRecords)
        // are intentionally NOT granted here — the @aws/agentcore-cdk L3 RuntimeExecutionRole
        // already grants them on '*'. This statement only adds the Logs Insights *query*
        // APIs (StartQuery/GetQueryResults/StopQuery) and broadens log-group scope, which
        // the L3 does not provide.
        new iam.PolicyStatement({
          sid: 'CloudWatchLogsInsights',
          actions: [
            'logs:FilterLogEvents',
            'logs:GetLogEvents',
            'logs:DescribeLogGroups',
            'logs:DescribeLogStreams',
            'logs:StartQuery',
            'logs:GetQueryResults',
            'logs:StopQuery',
          ],
          resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:*`],
        }),
      ];

      // Jira Identity permissions (only when Jira is configured)
      // GetResourceOauth2Token supports resource-level scoping to oauth2credentialprovider ARNs.
      // Scoped to '*' here because the provider ARN is not easily resolvable at synth time
      // (it's created by a custom resource, not a standard CFN resource with a Ref).
      // TODO: Scope to specific provider ARN once the Jira OAuth custom resource exports its ARN.
      if (jiraClientId) {
        statements.push(
          new iam.PolicyStatement({
            sid: 'AgentCoreIdentityJiraAccess',
            actions: ['bedrock-agentcore:GetResourceOauth2Token', 'bedrock-agentcore:GetWorkloadAccessToken'],
            resources: ['*'],
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
    // codifies this so it no longer has to be enabled manually in the console.
    if (spec.onlineEvalConfigs && spec.onlineEvalConfigs.length > 0) {
      this.enableTransactionSearch();
    }

    // ─── Step 7: Stack outputs ─────────────────────────────────────
    new CfnOutput(this, 'StackNameOutput', {
      description: 'Name of the CloudFormation Stack',
      value: this.stackName,
    });
  }

  /**
   * STEP: OBSERVABILITY — Enable CloudWatch Transaction Search via custom resource.
   *
   * There is no native CloudFormation type for Transaction Search. This provisions
   * a custom-resource Lambda that calls the X-Ray control plane to:
   *   1. Route trace segments to CloudWatch Logs (creates the `aws/spans` log group)
   *   2. Set the span indexing sampling percentage (100% for full searchability)
   *
   * This is an account- and region-level setting. On stack delete it is intentionally
   * left enabled (other agents in the account may depend on it).
   */
  private enableTransactionSearch(): void {
    const projectRoot = path.resolve(process.cwd(), '..', '..');

    const txnSearchFn = new lambda_.Function(this, 'TransactionSearchFn', {
      runtime: lambda_.Runtime.PYTHON_3_11,
      handler: 'infra.transaction_search.handler',
      timeout: cdk.Duration.minutes(2),
      memorySize: 256,
      code: lambda_.Code.fromAsset(path.join(projectRoot, 'lambdas')),
    });

    // X-Ray control-plane permissions needed to configure Transaction Search.
    txnSearchFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'TransactionSearchConfig',
        actions: [
          'xray:UpdateTraceSegmentDestination',
          'xray:GetTraceSegmentDestination',
          'xray:UpdateIndexingRule',
          'xray:GetIndexingRules',
          // Transaction Search writes spans to a CloudWatch Logs resource policy;
          // these allow X-Ray to set up the aws/spans log group destination.
          'logs:CreateLogGroup',
          'logs:PutResourcePolicy',
          'logs:DescribeResourcePolicies',
          'logs:DescribeLogGroups',
        ],
        resources: ['*'],
      })
    );

    const provider = new cr.Provider(this, 'TransactionSearchProvider', {
      onEventHandler: txnSearchFn,
    });

    new cdk.CustomResource(this, 'EnableTransactionSearch', {
      serviceToken: provider.serviceToken,
      properties: {
        IndexingPercentage: process.env.TXN_SEARCH_INDEXING_PERCENTAGE || '100',
        // Bump to force re-run on deploy when needed.
        Version: '1',
      },
    });

    new CfnOutput(this, 'TransactionSearchEnabled', {
      description: 'CloudWatch Transaction Search routes X-Ray spans to the aws/spans log group',
      value: 'CloudWatchLogs',
    });
  }

  /**
   * Replace placeholder Lambda ARNs in the MCP spec with real function ARNs.
   *
   * Iterates through gateway targets of type `lambdaFunctionArn` and replaces
   * any ARN containing "PLACEHOLDER" with the real ARN from the lambdaArnMap.
   * Targets without a matching real ARN are removed (e.g. query-kb when no KB_ID).
   */
  private patchMcpSpecArns(mcpSpec: AgentCoreMcpSpec, lambdaArnMap: Record<string, string>): AgentCoreMcpSpec {
    // Deep-clone to avoid mutating the original
    const patched = JSON.parse(JSON.stringify(mcpSpec));

    for (const gateway of patched.agentCoreGateways ?? []) {
      gateway.targets = (gateway.targets ?? []).filter((target: any) => {
        if (target.targetType === 'lambdaFunctionArn' && target.lambdaFunctionArn) {
          const realArn = lambdaArnMap[target.name];
          if (realArn) {
            target.lambdaFunctionArn.lambdaArn = realArn;
            return true;
          }
          // No real ARN available (e.g. KB not configured) — remove target
          return false;
        }
        return true;
      });
    }

    return patched;
  }

  /**
   * STEP: IDENTITY — Create Atlassian OAuth2 credential provider (conditional).
   *
   * Provisions a Secrets Manager secret for the client_secret and a custom
   * resource that registers an AtlassianOauth2 provider with AgentCore Identity.
   * The agent uses @requires_access_token(auth_flow="USER_FEDERATION") at
   * runtime to obtain Jira access tokens — it never sees the secret.
   *
   * Only called when JIRA_OAUTH_CLIENT_ID is set at deploy time.
   */
  private createJiraOauthProvider(clientId: string): string {
    const projectRoot = path.resolve(process.cwd(), '..', '..');
    const clientSecret = process.env.JIRA_OAUTH_CLIENT_SECRET || '';
    const providerName = `${this.stackName.toLowerCase().replace(/[^a-z0-9]/g, '')}_jira_3lo`;

    // Secrets Manager secret for the Atlassian client_secret
    // SECURITY: In production, store the secret externally (e.g., via AWS CLI or console)
    // and reference it by ARN instead of passing the value through CloudFormation.
    // This approach is acceptable for samples/demos only — the secret value will be
    // visible in the CloudFormation template and cdk.out/ synthesis output.
    if (!clientSecret) {
      throw new Error(
        'JIRA_OAUTH_CLIENT_SECRET is required when JIRA_OAUTH_CLIENT_ID is set. ' +
          'Set it in .env or as an environment variable.'
      );
    }
    const jiraSecret = new cdk.aws_secretsmanager.Secret(this, 'JiraOauthSecret', {
      description:
        'Atlassian 3LO client_secret (loaded into AgentCore Identity). For production, replace with externally-managed secret.',
      secretStringValue: cdk.SecretValue.unsafePlainText(JSON.stringify({ client_secret: clientSecret })),
    });

    // Custom resource Lambda for OAuth provider lifecycle
    const providerFn = new lambda_.Function(this, 'JiraOauthProviderFn', {
      runtime: lambda_.Runtime.PYTHON_3_11,
      handler: 'infra.jira_oauth_provider.handler',
      timeout: cdk.Duration.minutes(3),
      memorySize: 256,
      code: lambda_.Code.fromAsset(path.join(projectRoot, 'lambdas')),
    });
    jiraSecret.grantRead(providerFn);
    providerFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'bedrock-agentcore:CreateOauth2CredentialProvider',
          'bedrock-agentcore:UpdateOauth2CredentialProvider',
          'bedrock-agentcore:DeleteOauth2CredentialProvider',
          'bedrock-agentcore:GetOauth2CredentialProvider',
        ],
        resources: ['*'],
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
        SecretArn: jiraSecret.secretArn,
        Version: '1',
      },
    });

    // Outputs for post-deploy setup (callback URL registration)
    new CfnOutput(this, 'JiraOauthProviderName', { value: providerName });
    new CfnOutput(this, 'JiraOauthCallbackUrl', {
      value: jiraCr.getAttString('CallbackUrl'),
      description: 'Add this URL to the Atlassian OAuth app allowed callback URLs',
    });

    return providerName;
  }
}
