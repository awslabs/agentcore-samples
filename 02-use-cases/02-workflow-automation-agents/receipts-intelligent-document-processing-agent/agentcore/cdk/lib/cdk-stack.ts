import {
  AgentCoreApplication,
  AgentCoreMcp,
  type AgentCoreMcpSpec,
  type AgentCoreProjectSpec,
} from '@aws/agentcore-cdk';
import * as cdk from 'aws-cdk-lib';
import { CfnOutput, Stack, type StackProps } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda_ from 'aws-cdk-lib/aws-lambda';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import * as path from 'path';
import { InfraConstruct } from './infra-construct';

export interface AgentCoreStackProps extends StackProps {
  /** The AgentCore project specification (agentcore/agentcore.json). */
  spec: AgentCoreProjectSpec;
  /** The Gateway spec from the same file. */
  mcpSpec?: AgentCoreMcpSpec;
  /** Credential provider ARNs from deployed state, if any. */
  credentials?: Record<string, { credentialProviderArn: string; clientSecretArn?: string }>;
}

/** The pipeline Runtime (receipts in, expenses out) and the chat Runtime (questions in). */
const PIPELINE_AGENT = 'receiptsagent';
const CHAT_AGENT = 'receiptschat';
/** Created by scripts/chat_online_eval.py; see Step 8. */
const CHAT_ONLINE_EVAL_CONFIG = 'ReceiptsAgent_ChatLive';

/**
 * CDK Stack: Receipts IDP agent with its evaluators.
 *
 * 1. InfraConstruct: DynamoDB, S3 inbox, tool and pipeline Lambdas, SQS, EventBridge,
 *    the AppConfig ladder and its controller, Cognito, the identity KMS key.
 * 2. AgentCoreApplication (from agentcore.json): the two Runtimes, the code-based
 *    evaluators and the online evaluation configs.
 * 3. AgentCoreMcp: the Gateway, its Lambda targets with real ARNs, and the Cedar policies.
 *
 * Deployment: ./deploy.sh, which runs `agentcore deploy` and then creates the chat online
 * evaluation config (see Step 8).
 */
export class AgentCoreStack extends Stack {
  public readonly application: AgentCoreApplication;
  public readonly infra: InfraConstruct;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);
    const { spec, mcpSpec, credentials } = props;

    // ─── Step 1: supplementary infrastructure ─────────────────────────────
    this.infra = new InfraConstruct(this, 'Infra', { destroyOnDelete: true });

    // ─── Step 2: real Lambda ARNs and Cognito values into the Gateway spec ─
    const patchedMcpSpec = mcpSpec ? this.patchMcpSpec(mcpSpec) : undefined;

    // ─── Step 3: Runtimes, evaluators, online evaluation (agentcore.json) ──
    this.application = new AgentCoreApplication(this, 'Application', { spec });

    // ─── Step 4: Gateway, targets, Cedar policy engine ─────────────────────
    if (patchedMcpSpec?.agentCoreGateways && patchedMcpSpec.agentCoreGateways.length > 0) {
      new AgentCoreMcp(this, 'Mcp', {
        projectName: spec.name,
        mcpSpec: patchedMcpSpec,
        agentCoreApplication: this.application,
        credentials,
        projectTags: spec.tags,
      });
      this.orderGatewayTargetsAfterRolePolicy();
      this.removeGatewayTargetOutputs();
    }

    // ─── Step 5: configure both Runtimes ───────────────────────────────────
    const pipeline = this.runtime(PIPELINE_AGENT);
    const chat = this.runtime(CHAT_AGENT);

    const gatewayCfn = this.node
      .findAll()
      .find(c => (c as cdk.CfnResource).cfnResourceType === 'AWS::BedrockAgentCore::Gateway') as
      | cdk.CfnResource
      | undefined;

    // Both Runtimes reach the Gateway as the agent itself, over Cognito client_credentials
    // (ADR-0004). The client secret is injected at deploy time (ADR-0014).
    for (const runtime of [pipeline, chat]) {
      if (gatewayCfn) {
        runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_URL', gatewayCfn.getAtt('GatewayUrl').toString());
      }
      runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_CLIENT_ID', this.infra.userPoolClient.userPoolClientId);
      runtime.addEnvironmentVariable(
        'AGENTCORE_GATEWAY_CLIENT_SECRET',
        this.infra.userPoolClient.userPoolClientSecret.unsafeUnwrap()
      );
      runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_OAUTH_SCOPES', 'agentcore/invoke');
      runtime.addEnvironmentVariable('IDENTITY_KEY_ID', this.infra.identityKey.keyId);
      runtime.addToPolicy(
        new iam.PolicyStatement({
          sid: 'BedrockInvokeModel',
          actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
          // Global inference profiles route to foundation models in any Region.
          resources: [
            `arn:${this.partition}:bedrock:*::foundation-model/anthropic.*`,
            `arn:${this.partition}:bedrock:*:${this.account}:inference-profile/*`,
          ],
        })
      );
      runtime.addToPolicy(
        new iam.PolicyStatement({
          sid: 'VerifyChatIdentity',
          actions: ['kms:VerifyMac'],
          resources: [this.infra.identityKey.keyArn],
        })
      );
    }

    // The pipeline Runtime additionally reads receipts, the ladder, and writes the ledger.
    pipeline.addEnvironmentVariable('APPCONFIG_APPLICATION', this.infra.appConfigApplicationId);
    pipeline.addEnvironmentVariable('APPCONFIG_ENVIRONMENT', this.infra.appConfigEnvironmentId);
    pipeline.addEnvironmentVariable('APPCONFIG_PROFILE', this.infra.appConfigProfileId);
    pipeline.addEnvironmentVariable('DEFER_QUEUE_URL', this.infra.deferQueue.queueUrl);
    pipeline.addEnvironmentVariable('RUN_EVENT_BUS', this.infra.runBus.eventBusName);
    // Lets the live step-down test inject a simulated 503 through the payload.
    pipeline.addEnvironmentVariable('ALLOW_FAULT_INJECTION', 'true');
    pipeline.addToPolicy(
      new iam.PolicyStatement({ sid: 'TextractOcr', actions: ['textract:AnalyzeExpense'], resources: ['*'] })
    );
    pipeline.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ReadInbox',
        actions: ['s3:GetObject'],
        resources: [this.infra.inbox.arnForObjects('*')],
      })
    );
    pipeline.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ReadLadder',
        actions: ['appconfig:StartConfigurationSession', 'appconfig:GetLatestConfiguration'],
        resources: [
          `arn:${this.partition}:appconfig:${this.region}:${this.account}:application/${this.infra.appConfigApplicationId}/*`,
        ],
      })
    );
    pipeline.addToPolicy(
      new iam.PolicyStatement({
        sid: 'LadderMetrics',
        actions: ['cloudwatch:PutMetricData'],
        resources: ['*'],
        conditions: { StringEquals: { 'cloudwatch:namespace': 'ReceiptsAgent/Ladder' } },
      })
    );
    pipeline.addToPolicy(
      new iam.PolicyStatement({
        sid: 'RunLedger',
        actions: ['events:PutEvents'],
        resources: [this.infra.runBus.eventBusArn],
      })
    );
    pipeline.addToPolicy(
      new iam.PolicyStatement({
        sid: 'DeferQueue',
        actions: ['sqs:SendMessage'],
        resources: [this.infra.deferQueue.queueArn],
      })
    );

    // ─── Step 6: front door and drain invoke the pipeline Runtime ──────────
    for (const fn of [this.infra.triggerFn, this.infra.drainFn]) {
      pipeline.grantInvoke(fn);
      // The service authorizes the runtime AND its endpoint.
      fn.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ['bedrock-agentcore:InvokeAgentRuntime'],
          resources: [`${pipeline.runtimeArn}/runtime-endpoint/*`],
        })
      );
    }
    this.infra.triggerFn.addEnvironment('AGENTCORE_RUNTIME_ARN', pipeline.runtimeArn);
    this.infra.drainFn.addEnvironment('RUNTIME_ARN', pipeline.runtimeArn);

    // ─── Step 7: Transaction Search, the span source for online evaluation ─
    this.enableTransactionSearch();

    // ─── Step 8: execution role for the chat online evaluation config ──────
    // The chat config uses managed third-party evaluators (ThirdParty.DeepEval.*). The
    // AgentCore API accepts those ids, but the CloudFormation resource schema does not yet,
    // so scripts/chat_online_eval.py creates that one config through the API after deploy.
    const chatEvalRole = this.chatOnlineEvalRole(chat.runtimeId);

    // ─── Outputs ───────────────────────────────────────────────────────────
    new CfnOutput(this, 'RuntimeArn', { description: 'Pipeline Runtime ARN', value: pipeline.runtimeArn });
    new CfnOutput(this, 'ChatRuntimeArn', { description: 'Chat Runtime ARN', value: chat.runtimeArn });
    new CfnOutput(this, 'ChatOnlineEvalRoleArn', {
      description: 'Execution role for the chat online evaluation config (created by scripts/chat_online_eval.py)',
      value: chatEvalRole.roleArn,
    });
    new CfnOutput(this, 'StackNameOutput', { description: 'CloudFormation stack name', value: this.stackName });
  }

  /**
   * The same permissions the AgentCore construct grants its own online-config roles, scoped to
   * the chat Runtime's log group and the ChatLive results log group.
   */
  private chatOnlineEvalRole(chatRuntimeId: string): iam.Role {
    const configName = `${CHAT_ONLINE_EVAL_CONFIG}`;
    const logs = (name: string) => `arn:${this.partition}:logs:${this.region}:${this.account}:log-group:${name}`;
    const role = new iam.Role(this, 'ChatOnlineEvalRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
      description: `Online evaluation execution role for ${configName}`,
    });
    role.addToPolicy(new iam.PolicyStatement({ actions: ['logs:DescribeLogGroups'], resources: ['*'] }));
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['logs:GetLogEvents', 'logs:FilterLogEvents', 'logs:DescribeLogStreams', 'logs:StartQuery', 'logs:GetQueryResults'],
        resources: [logs(`/aws/bedrock-agentcore/runtimes/${chatRuntimeId}*`), logs('aws/spans*')],
      })
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'],
        resources: [logs(`/aws/bedrock-agentcore/evaluations/results/${configName}*`)],
      })
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['logs:DescribeIndexPolicies', 'logs:PutIndexPolicy'],
        resources: [logs('aws/spans')],
      })
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        resources: [
          `arn:${this.partition}:bedrock:*::foundation-model/*`,
          `arn:${this.partition}:bedrock:*:*:inference-profile/*`,
        ],
      })
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['cloudwatch:GenerateQuery', 'cloudwatch:GenerateQueryResultsSummary'],
        resources: ['*'],
      })
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:GetOnlineEvaluationConfig'],
        resources: [
          `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:online-evaluation-config/${configName}-*`,
        ],
      })
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:StartBatchEvaluation'],
        resources: [`arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:batch-evaluate/*`],
      })
    );
    return role;
  }

  private runtime(agentName: string) {
    const env = this.application.environments.get(agentName);
    if (!env) {
      throw new Error(`Agent environment "${agentName}" not found; check runtimes in agentcore.json`);
    }
    return env.runtime;
  }

  /**
   * Replace PLACEHOLDER_* Lambda ARNs with real ones (keyed by target name) and point the
   * Gateway's JWT authorizer at this stack's Cognito pool and client.
   */
  private patchMcpSpec(mcpSpec: AgentCoreMcpSpec): AgentCoreMcpSpec {
    const patched = JSON.parse(JSON.stringify(mcpSpec));
    for (const gateway of patched.agentCoreGateways ?? []) {
      gateway.targets = (gateway.targets ?? []).filter((target: Record<string, unknown>) => {
        if (target.targetType === 'lambdaFunctionArn' && target.lambdaFunctionArn) {
          const realArn = this.infra.lambdaArnMap[target.name as string];
          if (!realArn) {
            throw new Error(`No Lambda for Gateway target "${String(target.name)}"; add it to InfraConstruct`);
          }
          (target.lambdaFunctionArn as Record<string, string>).lambdaArn = realArn;
        }
        return true;
      });
      const jwt = gateway.authorizerConfiguration?.customJwtAuthorizer;
      if (jwt) {
        jwt.discoveryUrl = this.infra.cognitoDiscoveryUrl;
        jwt.allowedClients = [this.infra.userPoolClient.userPoolClientId];
      }
    }
    return patched;
  }

  /**
   * Targets must wait for the Gateway role's invoke permission. The L3 construct does not
   * add this dependency (verified on alpha.39); without it the first deploy can race.
   */
  private orderGatewayTargetsAfterRolePolicy(): void {
    const rolePolicy = this.node
      .findAll()
      .find(
        c =>
          (c as cdk.CfnResource).cfnResourceType === 'AWS::IAM::Policy' &&
          c.node.path.includes('Gateway') &&
          c.node.path.includes('Role') &&
          c.node.path.includes('DefaultPolicy')
      ) as cdk.CfnResource | undefined;
    if (!rolePolicy) return;
    for (const c of this.node.findAll()) {
      if ((c as cdk.CfnResource).cfnResourceType === 'AWS::BedrockAgentCore::GatewayTarget') {
        (c as cdk.CfnResource).addDependency(rolePolicy);
      }
    }
  }

  /**
   * The CLI parses `Gateway<Name>...Output` keys into its deployed state, and mis-reads the
   * per-target outputs as gateways without an ARN. Nothing reads them, so drop them. Match
   * by id: the L3 bundles its own aws-cdk-lib, so `instanceof CfnOutput` is unreliable.
   */
  private removeGatewayTargetOutputs(): void {
    for (const child of this.node.findAll()) {
      if (/^GatewayTarget.*Output$/.test(child.node.id)) {
        child.node.scope?.node.tryRemoveChild(child.node.id);
      }
    }
  }

  /**
   * Route X-Ray spans to CloudWatch Logs (`aws/spans`), where online evaluation reads them.
   * Account- and Region-level; left enabled on delete because other stacks may rely on it.
   */
  private enableTransactionSearch(): void {
    const projectRoot = path.resolve(process.cwd(), '..', '..');
    const fn = new lambda_.Function(this, 'TransactionSearchFn', {
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: 'transaction_search.handler',
      timeout: cdk.Duration.minutes(2),
      memorySize: 256,
      code: lambda_.Code.fromAsset(path.join(projectRoot, 'lambdas', 'infra')),
    });
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'xray:UpdateTraceSegmentDestination',
          'xray:GetTraceSegmentDestination',
          'xray:UpdateIndexingRule',
          'xray:GetIndexingRules',
          'logs:CreateLogGroup',
          'logs:PutResourcePolicy',
          'logs:DescribeResourcePolicies',
          'logs:DescribeLogGroups',
        ],
        resources: ['*'],
      })
    );
    const provider = new cr.Provider(this, 'TransactionSearchProvider', { onEventHandler: fn });
    new cdk.CustomResource(this, 'EnableTransactionSearch', {
      serviceToken: provider.serviceToken,
      properties: { IndexingPercentage: '100', Version: '1' },
    });
  }
}
