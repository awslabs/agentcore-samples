import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import * as fs from 'fs';
import * as path from 'path';
import { AgentCoreStack } from '../lib/cdk-stack';

// Synthesizes the stack from the real agentcore.json with placeholder Cognito values.
// No AWS calls are made.
function synth(): Template {
  process.env.COGNITO_DISCOVERY_URL =
    'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_test/.well-known/openid-configuration';
  process.env.AGENTCORE_GATEWAY_CLIENT_ID = 'm2m-client';
  process.env.FRONTDOOR_USER_CLIENT_ID = 'user-client';

  const spec = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), '..', 'agentcore.json'), 'utf8'));
  const mcpSpec = { agentCoreGateways: spec.agentCoreGateways };
  const app = new cdk.App();
  const stack = new AgentCoreStack(app, 'TestStack', {
    spec,
    mcpSpec,
    env: { account: '111111111111', region: 'us-east-1' },
  });
  return Template.fromStack(stack);
}

describe('agent-as-mcp-server stack', () => {
  const template = synth();

  test('runtime uses the MCP protocol', () => {
    template.hasResourceProperties('AWS::BedrockAgentCore::Runtime', {
      ProtocolConfiguration: 'MCP',
    });
  });

  test('two gateways, each accepting only its own Cognito client', () => {
    template.resourceCountIs('AWS::BedrockAgentCore::Gateway', 2);
    template.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      Name: Match.stringLikeRegexp('ExpenseToolsGateway'),
      AuthorizerConfiguration: { CustomJWTAuthorizer: Match.objectLike({ AllowedClients: ['m2m-client'] }) },
    });
    template.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      Name: Match.stringLikeRegexp('FrontDoorGateway'),
      AuthorizerConfiguration: { CustomJWTAuthorizer: Match.objectLike({ AllowedClients: ['user-client'] }) },
    });
  });

  test('front door targets the runtime as an MCP server, signed with the gateway IAM role', () => {
    template.hasResourceProperties('AWS::BedrockAgentCore::GatewayTarget', {
      Name: 'expense-agent',
      TargetConfiguration: { Mcp: { McpServer: Match.anyValue() } },
      CredentialProviderConfigurations: [
        {
          CredentialProviderType: 'GATEWAY_IAM_ROLE',
          CredentialProvider: { IamCredentialProvider: { Service: 'bedrock-agentcore', Region: 'us-east-1' } },
        },
      ],
    });
  });

  test('tools gateway targets the Lambda', () => {
    template.hasResourceProperties('AWS::BedrockAgentCore::GatewayTarget', {
      Name: 'expense-tools',
      TargetConfiguration: { Mcp: { Lambda: Match.anyValue() } },
    });
  });
});
