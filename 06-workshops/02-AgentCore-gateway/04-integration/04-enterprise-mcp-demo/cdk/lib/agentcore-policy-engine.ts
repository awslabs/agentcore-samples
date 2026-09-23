import { Construct } from 'constructs';
import { CustomResource, Duration } from 'aws-cdk-lib';
import { Function as LambdaFunction, Runtime, Code } from 'aws-cdk-lib/aws-lambda';
import { PolicyStatement, Effect, Policy, IRole } from 'aws-cdk-lib/aws-iam';
import * as path from 'path';
import { Provider } from 'aws-cdk-lib/custom-resources';
import { RetentionDays } from 'aws-cdk-lib/aws-logs';
import * as lambda from "aws-cdk-lib/aws-lambda";

export interface AgentCorePolicyEngineProps {
  readonly policyEngineName: string;
  readonly description?: string;
  readonly region: string;
  readonly gatewayRole?: IRole;
}

export interface PolicyProps {
  readonly policyName: string;
  readonly description?: string;
  readonly policyStatement: string;
}

/**
 * Construct that manages Bedrock AgentCore Policy Engine and policies.
 *
 * This construct:
 * 1. Creates a Policy Engine
 * 2. Provides methods to add policies to the engine
 * 3. Each policy has a name and statement
 */
export class AgentCorePolicyEngine extends Construct {
  public readonly policyFunction: LambdaFunction;
  public readonly policyEngineResource: CustomResource;
  public readonly policyEngineId: string;
  public readonly policyEngineArn: string;
  private readonly provider: Provider;
  private readonly policies: Map<string, CustomResource> = new Map();

  constructor(scope: Construct, id: string, props: AgentCorePolicyEngineProps) {
    super(scope, id);

    // Create the Lambda function that will handle policy engine and policy operations
    this.policyFunction = new lambda.Function(
          this,
          "PolicyFunction",
          {
            runtime: Runtime.PYTHON_3_12,
            handler: 'lambda_function.lambda_handler',
            description: 'Lambda to setup Bedrock AgentCore policy Engine',
            code: Code.fromAsset(path.join(__dirname, "../lambda/agentcore-policy-engine/"), {
              bundling: {
                image: Runtime.PYTHON_3_12.bundlingImage,
                command: [
                  "bash",
                  "-c",
                  [
                    "pip install -r requirements.txt -t /asset-output",
                    "cp agentcore_policy_engine.py /asset-output/lambda_function.py",
                  ].join(" && "),
                ],
              },
            }),
            timeout: Duration.minutes(2),
            memorySize: 256,
          }
        );

    // Grant permissions to manage policy engines and policies
    this.policyFunction.addToRolePolicy(
      new PolicyStatement({
        effect: Effect.ALLOW,
        actions: ['bedrock-agentcore:*'],
        resources: ['*'],
      }),
    );
    this.policyFunction.addToRolePolicy(
      new PolicyStatement({
        effect: Effect.ALLOW,
        actions: ['iam:GetRole', 'iam:GetRolePolicy', 'iam:ListAttachedRolePolicies', 'iam:ListRolePolicies'],
        resources: ["arn:aws:iam::*:role/*"],
      }),
    );

    // Grant permission to pass the gateway role if provided
    if (props.gatewayRole) {
      this.policyFunction.addToRolePolicy(
        new PolicyStatement({
          effect: Effect.ALLOW,
          actions: ['iam:PassRole'],
          resources: [props.gatewayRole.roleArn],
        }),
      );
    }

    // Create the custom resource provider
    this.provider = new Provider(this, 'Provider', {
      onEventHandler: this.policyFunction,
      logRetention: RetentionDays.ONE_MONTH,
    });

    // Create the policy engine
    this.policyEngineResource = new CustomResource(this, 'PolicyEngineResource', {
      serviceToken: this.provider.serviceToken,
      properties: {
        ResourceType: 'PolicyEngine',
        PolicyEngineName: props.policyEngineName,
        Description: props.description ?? `Policy Engine: ${props.policyEngineName}`,
        Region: props.region,
        Date: Date.now().toString(),
      },
    });

    // Extract policy engine ID
    this.policyEngineId = this.policyEngineResource.getAttString('PolicyEngineId');
    this.policyEngineArn = this.policyEngineResource.getAttString('PolicyEngineArn');
  }

  /**
   * Add a policy to the policy engine
   * @param policyName - The name of the policy
   * @param description - The description of the policy
   * @param policyStatement - The Cedar policy statement
   * @returns The policy ID
   */
  public addPolicy(policyName: string, description: string, policyStatement: string): string {
    // Check if policy with this name already exists
    if (this.policies.has(policyName)) {
      throw new Error(`Policy with name '${policyName}' already exists`);
    }

    // Create a custom resource for the policy
    const policyResource = new CustomResource(this, `Policy-${policyName}`, {
      serviceToken: this.provider.serviceToken,
      properties: {
        ResourceType: 'Policy',
        PolicyEngineId: this.policyEngineId,
        PolicyName: policyName,
        PolicyDescription: description,
        PolicyEngineArn: this.policyEngineArn,
        PolicyStatement: policyStatement,
        Date: Date.now().toString(),
      },
    });

    // Make sure the policy is created after the engine
    policyResource.node.addDependency(this.policyEngineResource);

    // Store the policy resource
    this.policies.set(policyName, policyResource);

    // Return the policy ID
    return policyResource.getAttString('PolicyId');
  }

  /**
   * Get the policy resource by name
   * @param policyName - The name of the policy
   * @returns The CustomResource for the policy, or undefined if not found
   */
  public getPolicy(policyName: string): CustomResource | undefined {
    return this.policies.get(policyName);
  }

  /**
   * Get all policy names
   * @returns Array of policy names
   */
  public getPolicyNames(): string[] {
    return Array.from(this.policies.keys());
  }

  public associateWithGateway(gatewayId: string, policyEngineConfigurationMode: string) {
    // Create a custom resource to associate the policy engine with the gateway
    const associationResource = new CustomResource(this, 'PolicyEngineGatewayAssociation', {
      serviceToken: this.provider.serviceToken,
      properties: {
        ResourceType: 'PolicyEngineGatewayAssociation',
        PolicyEngineId: this.policyEngineId,
        PolicyEngineArn: this.policyEngineArn,
        GatewayId: gatewayId,
        PolicyEngineConfigurationMode:policyEngineConfigurationMode,
        Date: Date.now().toString(),
      },
    });

    // Ensure association happens after the policy engine is created
    associationResource.node.addDependency(this.policyEngineResource);

    // Ensure association happens after all policies are added
    for (const policyResource of this.policies.values()) {
      associationResource.node.addDependency(policyResource);
    }

    return associationResource;
  }
}
