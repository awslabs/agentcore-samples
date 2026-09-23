import { LambdaClient, InvokeCommand } from '@aws-sdk/client-lambda';

/**
 * Service for invoking AgentCore Runtime agents via Python Lambda bridge
 * 
 * This service invokes a Python Lambda that uses the AgentCore Starter Toolkit
 * to properly invoke the AgentCore Runtime. This bridges the gap between
 * TypeScript and the Python-only AgentCore SDK.
 */
export class AgentCoreService {
  private lambdaClient: LambdaClient;
  private invokerLambdaName: string;

  constructor() {
    this.lambdaClient = new LambdaClient({
      region: process.env.AWS_REGION || 'us-east-1'
    });
    
    // Lambda name will be set via environment variable
    this.invokerLambdaName = process.env.AGENTCORE_INVOKER_LAMBDA_NAME || '';
  }

  /**
   * Invoke AgentCore Runtime agent via Python Lambda bridge
   * 
   * @param agentArn - Agent ARN (not used, kept for compatibility)
   * @param payload - Payload to send to the agent
   * @returns Agent response
   */
  async invokeAgent(agentArn: string, payload: any): Promise<any> {
    console.log('AgentCore invocation started (via Python Lambda bridge)');
    console.log('Agent ARN:', agentArn);
    console.log('Payload:', JSON.stringify(payload, null, 2));
    console.log('Invoker Lambda:', this.invokerLambdaName);

    if (!this.invokerLambdaName) {
      throw new Error('AGENTCORE_INVOKER_LAMBDA_NAME environment variable not set');
    }

    try {
      // Invoke the Python Lambda that handles AgentCore Runtime invocation
      const command = new InvokeCommand({
        FunctionName: this.invokerLambdaName,
        InvocationType: 'RequestResponse',
        Payload: JSON.stringify({
          payload: payload
        })
      });

      console.log('Invoking Python Lambda bridge...');
      const response = await this.lambdaClient.send(command);

      // Parse Lambda response
      if (!response.Payload) {
        throw new Error('No payload in Lambda response');
      }

      const responseText = new TextDecoder().decode(response.Payload);
      console.log('Lambda response:', responseText);

      const lambdaResult = JSON.parse(responseText);

      // Check for Lambda errors
      if (lambdaResult.statusCode !== 200) {
        const errorBody = JSON.parse(lambdaResult.body);
        throw new Error(`AgentCore invocation failed: ${errorBody.error}`);
      }

      // Parse the successful response
      const resultBody = JSON.parse(lambdaResult.body);
      console.log('AgentCore Runtime response:', JSON.stringify(resultBody.response, null, 2));

      return resultBody.response;

    } catch (error: any) {
      console.error('AgentCore invocation error:', error);
      throw new Error(`Failed to invoke AgentCore Runtime: ${error.message}`);
    }
  }
}

export const agentCoreService = new AgentCoreService();
