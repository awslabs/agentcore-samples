import { AgentCoreApplication } from '@aws/agentcore-cdk';
import { Stack, aws_bedrockagentcore as agentcore, aws_iam as iam } from 'aws-cdk-lib';

export interface VpcPermissions {
  gatewayName: string;
  providers: { type: 'api-key' | 'oauth'; providerArn: string; secretArn: string }[];
  lambdaArns: string[];
}

/** SDK owns private targets; CDK still owns and bounds their gateway role. */
export function grantVpcPermissions(stack: Stack, app: AgentCoreApplication, config: VpcPermissions) {
  if (!Array.isArray(config.providers) || !Array.isArray(config.lambdaArns)) {
    throw new Error('Invalid local gateway-permissions.json');
  }
  const gateway = app.gateway(config.gatewayName);
  const exactArn = (arn: string, service: string) => {
    if (typeof arn !== 'string' || /[*?]/.test(arn)) throw new Error('Grants require exact ARNs');
    const parts = arn.split(':');
    if (parts[0] !== 'arn' || parts[2] !== service || parts[3] !== stack.region || parts[4] !== stack.account) {
      throw new Error(`Grant must belong to the deployment account/region: ${arn}`);
    }
  };
  for (const provider of config.providers) {
    exactArn(provider.providerArn, 'bedrock-agentcore');
    exactArn(provider.secretArn, 'secretsmanager');
    if (!['api-key', 'oauth'].includes(provider.type)) throw new Error('Unknown provider type');
    const segment = provider.type === 'api-key' ? 'apikeycredentialprovider' : 'oauth2credentialprovider';
    if (!provider.providerArn.includes(`:token-vault/default/${segment}/`)) throw new Error('Wrong provider ARN type');
    const grant = provider.type === 'api-key'
      ? agentcore.GatewayCredentialProvider.fromApiKeyIdentityArn(provider)
      : agentcore.GatewayCredentialProvider.fromOauthIdentityArn({ ...provider, scopes: [] });
    grant.grantNeededPermissionsToRole(gateway.resource);
    // Identity is named after the lowercase service gateway ID, not its display name.
    gateway.addToRolePolicy(new iam.PolicyStatement({
      actions: provider.type === 'api-key'
        ? [...agentcore.GATEWAY_WORKLOAD_IDENTITY_PERMS, ...agentcore.GATEWAY_API_KEY_PERMS]
        : [...agentcore.GATEWAY_WORKLOAD_IDENTITY_OAUTH_PERMS, ...agentcore.GATEWAY_OAUTH_PERMS,
           ...agentcore.GATEWAY_OAUTH_COMPLETE_AUTH_PERMS],
      resources: [stack.formatArn({ service: 'bedrock-agentcore',
        resource: 'workload-identity-directory', resourceName: `default/workload-identity/${gateway.gatewayId}` })],
    }));
  }
  for (const arn of config.lambdaArns) {
    exactArn(arn, 'lambda');
    if (!arn.includes(':function:')) throw new Error('Expected Lambda function ARN');
    gateway.addToRolePolicy(new iam.PolicyStatement({ actions: ['lambda:InvokeFunction'], resources: [arn] }));
  }
}
