import {
  AgentCoreApplication,
  AgentCoreMcp,
  type AgentCoreProjectSpec,
  type AgentCoreMcpSpec,
} from '@aws/agentcore-cdk';
import * as cdk from 'aws-cdk-lib';
import { CfnOutput, Stack, type StackProps } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { InfraConstruct } from './infra-construct';

export interface AgentCoreStackProps extends StackProps {
  /** The AgentCore project specification containing agents, memories, and credentials. */
  spec: AgentCoreProjectSpec;
  /** The MCP specification containing gateways and servers. */
  mcpSpec?: AgentCoreMcpSpec;
  /** Credential provider ARNs from deployed state, keyed by credential name. */
  credentials?: Record<string, { credentialProviderArn: string; clientSecretArn?: string }>;
}

/**
 * CDK Stack: Event-Driven Claims Agent
 *
 * Integrates:
 * 1. InfraConstruct — DynamoDB, S3, Lambda tools, SNS, EventBridge, Cognito
 * 2. AgentCoreApplication — Runtime, Memory, PolicyEngine, OnlineEval (from agentcore.json)
 * 3. AgentCoreMcp — Gateway + 6 Lambda targets with real ARNs from step 1
 *
 * Deployment: `agentcore deploy --target dev`
 */
export class AgentCoreStack extends Stack {
  public readonly application: AgentCoreApplication;
  public readonly infra: InfraConstruct;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const { spec, mcpSpec, credentials } = props;

    // ─── Step 1: Supplementary infrastructure ──────────────────────
    // Creates DynamoDB tables, Lambda tool functions, S3, EventBridge, SNS, Cognito.
    // Exposes lambdaArnMap for patching gateway targets.
    this.infra = new InfraConstruct(this, 'Infra', {
      destroyOnDelete: true,
    });

    // ─── Step 2: Patch mcpSpec with real Lambda ARNs + JWT authorizer ──
    const patchedMcpSpec = mcpSpec ? this.patchMcpSpecArns(mcpSpec, this.infra.lambdaArnMap) : undefined;

    // ─── Step 3: AgentCore Application (Runtime + Memory + Eval) ───
    this.application = new AgentCoreApplication(this, 'Application', { spec });

    // ─── Step 4: AgentCore MCP (Gateway + Targets + PolicyEngine) ──
    if (patchedMcpSpec?.agentCoreGateways && patchedMcpSpec.agentCoreGateways.length > 0) {
      new AgentCoreMcp(this, 'Mcp', {
        projectName: spec.name,
        mcpSpec: patchedMcpSpec,
        agentCoreApplication: this.application,
        credentials,
        projectTags: spec.tags,
      });

      // Order GatewayTargets after the gateway role policy so they deploy in a single clean pass.
      // NOTE: Verified against @aws/agentcore-cdk alpha.39 — the L3 construct does NOT add this
      // dependency itself; removing this block drops the DependsOn and reintroduces a deploy race
      // where targets are created before the gateway role's invoke permission is attached.
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

      // ─── Suppress mis-parsed Gateway TARGET outputs (CLI deployed-state quirk) ──
      // The @aws/agentcore-cdk construct emits one CfnOutput per gateway target named
      // `GatewayTarget<Name>IdOutput`. The agentcore CLI (preview.13) builds its
      // deployed-state by parsing CloudFormation output keys of the form
      // `Gateway<Name><Arn|Id|Url>Output` and grouping them as gateways — so these
      // target outputs get mis-parsed as gateways with no `gatewayArn`, producing a
      // non-fatal but alarming "gatewayArn: Too small" validation error after EVERY
      // deploy. The target Id outputs are not consumed by any script or test, so we
      // remove them here. The real gateway's Arn/Id/Url outputs are preserved, so the
      // CLI still records the gateway in deployed-state.json.
      //
      // NOTE: match by node id (not `instanceof CfnOutput`) — the L3 construct bundles
      // its own aws-cdk-lib copy, so cross-realm `instanceof` returns false. CfnOutput
      // ids end in `Output`, so the regex never matches the target resource constructs.
      for (const child of this.node.findAll()) {
        if (/^GatewayTarget.*Output$/.test(child.node.id)) {
          child.node.scope?.node.tryRemoveChild(child.node.id);
        }
      }
    }

    // ─── Steps 5+6: Configure the Runtime (env vars + permissions) ──
    // Use the typed AgentEnvironment API instead of walking the construct tree.
    const agentEnv = this.application.environments.get('claimsagent');
    if (!agentEnv) {
      throw new Error('Agent environment "claimsagent" not found in application.environments');
    }
    const runtime = agentEnv.runtime;

    // Gateway URL alias (agent code reads AGENTCORE_GATEWAY_URL). The Gateway is
    // not exposed as a typed property, so locate its CFN resource directly.
    const gatewayCfn = this.node
      .findAll()
      .find(c => (c as cdk.CfnResource).cfnResourceType === 'AWS::BedrockAgentCore::Gateway') as cdk.CfnResource | undefined;
    if (gatewayCfn) {
      runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_URL', gatewayCfn.getAtt('GatewayUrl').toString());
    }

    // Identity credential provider name — the Runtime uses @requires_access_token
    // with this provider name to get tokens from the AgentCore Identity vault.
    // No client secrets are injected into the Runtime environment.
    const credentialProvider = process.env.AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER || 'cognito-gateway-m2m';
    runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER', credentialProvider);
    runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_OAUTH_SCOPES', 'agentcore/invoke');

    // Wire trigger Lambda → Runtime (grantInvoke adds the IAM permission).
    runtime.grantInvoke(this.infra.triggerFn);
    this.infra.triggerFn.addEnvironment('AGENTCORE_RUNTIME_ARN', runtime.runtimeArn);

    // Fix: grantInvoke only grants on the runtime ARN, but the service evaluates
    // permissions on BOTH the runtime AND the endpoint (hierarchical authorization).
    // Add explicit permission on the endpoint wildcard.
    this.infra.triggerFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'InvokeAgentEndpoint',
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: [`${runtime.runtimeArn}/runtime-endpoint/*`],
      })
    );

    // Grant the Runtime permission to invoke the Bedrock model.
    runtime.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockInvokeModel',
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        resources: [
          `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-sonnet-4-6`,
          'arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6',
          'arn:aws:bedrock:*:*:inference-profile/*',
        ],
      })
    );

    // Output the Runtime ARN so test scripts (test_invoke.py, test_e2e.py, test_cedar.py)
    // can read it from CloudFormation outputs to invoke the deployed Runtime.
    new CfnOutput(this, 'RuntimeArn', {
      description: 'AgentCore Runtime ARN',
      value: runtime.runtimeArn,
    });

    // ─── Step 7: Outputs ──────────────────────────────────────────
    new CfnOutput(this, 'StackNameOutput', {
      description: 'CloudFormation Stack Name',
      value: this.stackName,
    });
  }

  /**
   * Replace placeholders in the MCP spec with real CDK-resolved values:
   * - Lambda target ARNs (PLACEHOLDER_* → function ARN from lambdaArnMap)
   * - CUSTOM_JWT authorizer discovery URL + allowed clients (Cognito values from infra)
   */
  private patchMcpSpecArns(mcpSpec: AgentCoreMcpSpec, lambdaArnMap: Record<string, string>): AgentCoreMcpSpec {
    const patched = JSON.parse(JSON.stringify(mcpSpec));
    for (const gateway of patched.agentCoreGateways ?? []) {
      gateway.targets = (gateway.targets ?? []).filter((target: Record<string, unknown>) => {
        if (target.targetType === 'lambdaFunctionArn' && target.lambdaFunctionArn) {
          const realArn = lambdaArnMap[target.name as string];
          if (realArn) {
            (target.lambdaFunctionArn as Record<string, string>).lambdaArn = realArn;
            return true;
          }
          return false;
        }
        return true;
      });

      // Patch CUSTOM_JWT authorizer placeholders with real Cognito values.
      const jwt = gateway.authorizerConfiguration?.customJwtAuthorizer;
      if (jwt) {
        jwt.discoveryUrl = this.infra.cognitoDiscoveryUrl;
        jwt.allowedClients = [this.infra.cognitoClientId];
      }
    }
    return patched;
  }
}
