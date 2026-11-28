import { AgentCoreApplication, type AgentCoreApplicationProps } from '@aws/agentcore-cdk';
import { CfnOutput, CfnResource, Stack, type StackProps } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { InfraConstruct } from './infra-construct';
import { boundRuntime } from './runtime-boundary';
import { addObservability } from './observability';

export interface AgentCoreStackProps extends StackProps {
  application: AgentCoreApplicationProps;
  notifications: boolean;
  discoveryUrl: string;
  clientId: string;
}

/** Custom v1 app, NOT the replaceable CLI scaffold. Fresh stacks only. */
export class AgentCoreStack extends Stack {
  public readonly application: AgentCoreApplication;
  public readonly infra: InfraConstruct;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);
    const application = props.application;
    const spec = structuredClone(application.spec);
    const prefix = `${spec.name}-${application.targetName}`;
    this.infra = new InfraConstruct(this, 'Infra', {
      projectRoot: application.projectRoot,
      namePrefix: prefix,
      notifications: props.notifications,
      destroyOnDelete: true,
    });
    for (const gateway of spec.agentCoreGateways) {
      for (const target of gateway.targets) {
        if (target.targetType !== 'lambdaFunctionArn' || !target.lambdaFunctionArn) throw new Error(`Unsupported target ${target.name}`);
        const arn = this.infra.lambdaArnMap[target.name];
        if (!arn) throw new Error(`No supplementary Lambda ARN for Gateway target "${target.name}"`);
        target.lambdaFunctionArn.lambdaArn = arn;
      }
      if (gateway.authorizerType !== 'CUSTOM_JWT') throw new Error('Gateway must use M2M CUSTOM_JWT');
      gateway.authorizerConfiguration = { customJwtAuthorizer: {
        discoveryUrl: props.discoveryUrl, allowedClients: [props.clientId],
      } };
      if (gateway.policyEngineConfiguration?.mode !== 'ENFORCE') throw new Error('Policy must ENFORCE');
    }
    this.application = new AgentCoreApplication(this, 'Application', { ...application, spec });
    const runtime = this.application.runtime('claimsagent');
    const gateway = this.application.gateway('ClaimsGateway');
    runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_URL', gateway.gatewayUrl);
    const credential = application.credentials?.['cognito-gateway-m2m'];
    if (!credential?.clientSecretArn) throw new Error('Owned Gateway credential/secret ARNs must be provisioned before synth');
    boundRuntime(this, this.application, credential.credentialProviderArn, credential.clientSecretArn);
    runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER', credential.credentialProviderArn.split('/').pop()!);
    runtime.addEnvironmentVariable('AGENTCORE_GATEWAY_OAUTH_SCOPES', 'agentcore/invoke');
    // v1 grantInvoke includes runtime and endpoint ARNs; no legacy duplicate grant.
    runtime.grantInvoke(this.infra.triggerFn);
    this.infra.triggerFn.addEnvironment('AGENTCORE_RUNTIME_ARN', runtime.runtimeArn);
    // Explicit ordering for imported Lambda targets: the Gateway role must be ready first.
    const policies = gateway.node.findAll().filter(c => (c as CfnResource).cfnResourceType === 'AWS::IAM::Policy');
    for (const child of this.application.node.findAll()) {
      if ((child as CfnResource).cfnResourceType === 'AWS::BedrockAgentCore::GatewayTarget') {
        for (const policy of policies) (child as CfnResource).addResourceDependency(policy as CfnResource);
      }
    }
    addObservability(this, this.application, prefix);
    new CfnOutput(this, 'RuntimeArn', { value: runtime.runtimeArn });
    new CfnOutput(this, 'GatewayUrl', { value: gateway.gatewayUrl });
    new CfnOutput(this, 'StackNameOutput', { value: this.stackName });
  }
}
