import {
  AgentCoreApplication,
  AgentCoreMcp,
  type AgentCoreProjectSpec,
  type AgentCoreMcpSpec,
} from '@aws/agentcore-cdk';
import * as cdk from 'aws-cdk-lib';
import { CfnOutput, Duration, Stack, type StackProps } from 'aws-cdk-lib';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import * as path from 'path';

/** Kept for compatibility with the vended bin/cdk.ts; this sample deploys no harnesses. */
export type HarnessConfig = Record<string, unknown>;

export interface AgentCoreStackProps extends StackProps {
  /** The AgentCore project specification (agentcore.json). */
  spec: AgentCoreProjectSpec;
  /** The MCP specification containing gateways and targets. */
  mcpSpec?: AgentCoreMcpSpec;
  /** Credential provider ARNs from deployed state, keyed by credential name. */
  credentials?: Record<string, { credentialProviderArn: string; clientSecretArn?: string }>;
  // Unused here but passed by the vended bin/cdk.ts; kept for signature compatibility.
  harnesses?: HarnessConfig[];
  connectorParametersByFile?: Record<string, Record<string, unknown>>;
  paymentSpec?: unknown[];
}

const RUNTIME_NAME = 'expense_agent';
const TOOLS_GATEWAY_NAME = 'ExpenseToolsGateway';
const FRONTDOOR_GATEWAY_NAME = 'FrontDoorGateway';
const TOOLS_TARGET_NAME = 'expense-tools';

/**
 * CDK Stack: Agent as an MCP server
 *
 *   MCP client (Claude / Kiro)
 *     -> FrontDoorGateway   (CUSTOM_JWT, Cognito user client, browser sign-in)
 *       -> expense_agent Runtime  (MCP protocol; gateway signs SigV4 with its own role)
 *         -> ExpenseToolsGateway  (CUSTOM_JWT, Cognito M2M client via AgentCore Identity)
 *           -> expense-tools Lambda
 *
 * agentcore.json declares the runtime, the credential provider and both gateways.
 * This stack adds what the CLI schema cannot express yet:
 *   1. the Lambda behind the tools gateway (ARN patched into the target),
 *   2. Cognito values patched into both CUSTOM_JWT authorizers,
 *   3. the front-door gateway's target: the runtime as an MCP server, reached with
 *      the gateway's IAM role (GATEWAY_IAM_ROLE),
 *   4. runtime env vars + Bedrock model permissions.
 */
export class AgentCoreStack extends Stack {
  public readonly application: AgentCoreApplication;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);
    const { spec, mcpSpec, credentials } = props;

    // ─── Cognito values (from .env, exported by deploy.sh) ─────────────────
    const cognito = {
      discoveryUrl: requireEnv('COGNITO_DISCOVERY_URL'),
      m2mClientId: requireEnv('AGENTCORE_GATEWAY_CLIENT_ID'),
      userClientId: requireEnv('FRONTDOOR_USER_CLIENT_ID'),
    };

    // ─── 1. Lambda behind the tools gateway ────────────────────────────────
    const toolsFn = new lambda.Function(this, 'ExpenseToolsFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      // The AgentCore CLI (and jest) run with cwd = agentcore/cdk, so the project root is two levels up.
      code: lambda.Code.fromAsset(path.resolve(process.cwd(), '..', '..', 'lambdas', 'expense_tools')),
      timeout: Duration.seconds(30),
      description: 'Mock expense business tools exposed as MCP tools via AgentCore Gateway',
      logGroup: new logs.LogGroup(this, 'ExpenseToolsLogGroup', {
        retention: logs.RetentionDays.ONE_WEEK,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    // ─── 2. Patch placeholders in the MCP spec ─────────────────────────────
    const patchedMcpSpec = mcpSpec ? this.patchMcpSpec(mcpSpec, toolsFn.functionArn, cognito) : undefined;

    // ─── AgentCore resources from agentcore.json ───────────────────────────
    this.application = new AgentCoreApplication(this, 'Application', { spec, credentials });

    const agentEnv = this.application.environments.get(RUNTIME_NAME);
    if (!agentEnv) {
      throw new Error(`Runtime "${RUNTIME_NAME}" not found in agentcore.json`);
    }
    const runtime = agentEnv.runtime;

    let mcp: AgentCoreMcp | undefined;
    if (patchedMcpSpec?.agentCoreGateways?.length) {
      mcp = new AgentCoreMcp(this, 'Mcp', {
        projectName: spec.name,
        mcpSpec: patchedMcpSpec,
        agentCoreApplication: this.application,
        credentials,
        projectTags: spec.tags,
      });
    }
    if (!mcp) {
      throw new Error('agentcore.json must declare the two gateways');
    }

    const toolsGateway = mcp.gateways.get(TOOLS_GATEWAY_NAME);
    const frontDoorGateway = mcp.gateways.get(FRONTDOOR_GATEWAY_NAME);
    if (!toolsGateway || !frontDoorGateway) {
      throw new Error(`Expected gateways "${TOOLS_GATEWAY_NAME}" and "${FRONTDOOR_GATEWAY_NAME}" in agentcore.json`);
    }

    // Order gateway targets after the gateway role policy (avoids a create-time race
    // where the target is validated before the role can invoke the Lambda).
    this.orderTargetsAfterRolePolicies();

    // ─── 3. Front door: runtime as an MCP-server target ────────────────────
    // The Gateway calls the Runtime's MCP endpoint and signs with its own IAM role.
    // Endpoint format: https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded ARN>/invocations
    const encodedRuntimeArn = cdk.Fn.join('%3A', cdk.Fn.split(':', cdk.Fn.join('%2F', cdk.Fn.split('/', runtime.runtimeArn))));
    const runtimeMcpEndpoint = `https://bedrock-agentcore.${this.region}.amazonaws.com/runtimes/${encodedRuntimeArn}/invocations?qualifier=DEFAULT`;

    const frontDoorRole = iam.Role.fromRoleArn(this, 'FrontDoorGatewayRole', frontDoorGateway.roleArn!, {
      mutable: true,
    });
    const invokeRuntimePolicy = new iam.Policy(this, 'FrontDoorInvokeRuntime', {
      statements: [
        new iam.PolicyStatement({
          sid: 'InvokeExpenseAgentRuntime',
          actions: ['bedrock-agentcore:InvokeAgentRuntime'],
          resources: [runtime.runtimeArn, `${runtime.runtimeArn}/runtime-endpoint/*`],
        }),
      ],
    });
    frontDoorRole.attachInlinePolicy(invokeRuntimePolicy);

    const agentTarget = new bedrockagentcore.CfnGatewayTarget(this, 'FrontDoorAgentTarget', {
      gatewayIdentifier: frontDoorGateway.attrGatewayIdentifier,
      name: 'expense-agent',
      description: 'The expense agent runtime, exposed as an MCP server (one tool: process_expense_report)',
      targetConfiguration: {
        mcp: {
          mcpServer: { endpoint: runtimeMcpEndpoint },
        },
      },
      // The gateway signs requests to the Runtime with its own role (SigV4 for the
      // bedrock-agentcore service). The service requires the signing target spelled out.
      credentialProviderConfigurations: [
        {
          credentialProviderType: 'GATEWAY_IAM_ROLE',
          credentialProvider: {
            iamCredentialProvider: { service: 'bedrock-agentcore', region: this.region },
          },
        },
      ],
    });
    agentTarget.node.addDependency(invokeRuntimePolicy);
    agentTarget.node.addDependency(runtime);

    // ─── 4. Runtime configuration ──────────────────────────────────────────
    // The agent reads the tools gateway URL from AGENTCORE_GATEWAY_URL.
    runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_URL', toolsGateway.attrGatewayUrl);

    runtime.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockInvokeModel',
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        resources: ['arn:aws:bedrock:*::foundation-model/*', 'arn:aws:bedrock:*:*:inference-profile/*'],
      })
    );

    // ─── Outputs ───────────────────────────────────────────────────────────
    new CfnOutput(this, 'RuntimeArn', { value: runtime.runtimeArn, description: 'AgentCore Runtime ARN' });
    new CfnOutput(this, 'RuntimeMcpEndpoint', {
      value: runtimeMcpEndpoint,
      description: 'Runtime MCP endpoint (IAM/SigV4) - use with mcp-proxy-for-aws from an IDE',
    });
    new CfnOutput(this, 'FrontDoorMcpUrl', {
      value: frontDoorGateway.attrGatewayUrl,
      description: 'Front-door MCP URL (OAuth) - paste into Claude custom connectors',
    });
    new CfnOutput(this, 'ToolsGatewayUrl', { value: toolsGateway.attrGatewayUrl, description: 'Tools gateway URL' });
    new CfnOutput(this, 'ToolsLambdaName', { value: toolsFn.functionName, description: 'Tools Lambda function name' });
    new CfnOutput(this, 'StackNameOutput', { value: this.stackName, description: 'CloudFormation stack name' });
  }

  /** Replace PLACEHOLDER_* values in agentcore.json with CDK-resolved values. */
  private patchMcpSpec(
    mcpSpec: AgentCoreMcpSpec,
    toolsLambdaArn: string,
    cognito: { discoveryUrl: string; m2mClientId: string; userClientId: string }
  ): AgentCoreMcpSpec {
    const patched = JSON.parse(JSON.stringify(mcpSpec));
    for (const gateway of patched.agentCoreGateways ?? []) {
      for (const target of gateway.targets ?? []) {
        if (target.targetType === 'lambdaFunctionArn' && target.name === TOOLS_TARGET_NAME) {
          target.lambdaFunctionArn.lambdaArn = toolsLambdaArn;
        }
      }
      const jwt = gateway.authorizerConfiguration?.customJwtAuthorizer;
      if (jwt) {
        jwt.discoveryUrl = cognito.discoveryUrl;
        jwt.allowedClients = [gateway.name === FRONTDOOR_GATEWAY_NAME ? cognito.userClientId : cognito.m2mClientId];
      }
    }
    return patched;
  }

  /** Make every GatewayTarget depend on every gateway role DefaultPolicy. */
  private orderTargetsAfterRolePolicies(): void {
    const all = this.node.findAll();
    const rolePolicies = all.filter(
      c =>
        (c as cdk.CfnResource).cfnResourceType === 'AWS::IAM::Policy' &&
        c.node.path.includes('Gateway') &&
        c.node.path.includes('Role')
    ) as cdk.CfnResource[];
    const targets = all.filter(
      c => (c as cdk.CfnResource).cfnResourceType === 'AWS::BedrockAgentCore::GatewayTarget'
    ) as cdk.CfnResource[];
    for (const target of targets) {
      for (const policy of rolePolicies) {
        target.addDependency(policy);
      }
    }
  }
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value || value.startsWith('PLACEHOLDER')) {
    throw new Error(`Environment variable ${name} is not set. Run ./scripts/setup_cognito.sh first (it writes .env).`);
  }
  return value;
}
