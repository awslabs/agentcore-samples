import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface BedrockKnowledgeBasePermissionsProps {
  /**
   * The name of the IAM role used by Bedrock for the knowledge base
   */
  roleName: string;

  /**
   * The ARN of the OpenSearch Serverless collection
   */
  collectionArn: string;

  /**
   * The ID of the OpenSearch Serverless collection
   */
  collectionId: string;

  /**
   * The AWS region
   */
  region: string;
}

/**
 * A construct that creates and attaches the necessary IAM policies for a Bedrock knowledge base
 * to access OpenSearch Serverless and embedding models.
 */
export class BedrockKnowledgeBasePermissions extends Construct {
  constructor(scope: Construct, id: string, props: BedrockKnowledgeBasePermissionsProps) {
    super(scope, id);

    // Get the existing IAM role
    const role = iam.Role.fromRoleName(this, 'BedrockKnowledgeBaseRole', props.roleName);

    // Create a policy for OpenSearch access
    const opensearchPolicy = new iam.Policy(this, 'OpenSearchAccessPolicy', {
      policyName: 'BedrockKnowledgeBaseOpenSearchAccess',
      statements: [
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: [
            'aoss:APIAccessAll',
            'aoss:BatchGetCollection',
            'aoss:CreateIndex',
            'aoss:DeleteIndex',
            'aoss:DescribeIndex',
            'aoss:ReadDocument',
            'aoss:WriteDocument',
            'aoss:UpdateIndex',
          ],
          resources: [props.collectionArn],
        }),
      ],
    });

    // Attach the OpenSearch policy to the role
    opensearchPolicy.attachToRole(role);

    // Create a policy for Bedrock model access
    const bedrockPolicy = new iam.Policy(this, 'BedrockModelAccessPolicy', {
      policyName: 'BedrockKnowledgeBaseModelAccess',
      statements: [
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
          resources: [
            // Titan embedding model for knowledge base
            `arn:aws:bedrock:${props.region}::foundation-model/amazon.titan-embed-text-v1`,
            // Claude Sonnet 4 model for agent responses
            `arn:aws:bedrock:${props.region}::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0`,
          ],
        }),
      ],
    });

    // Attach the Bedrock policy to the role
    bedrockPolicy.attachToRole(role);

    // Create an OpenSearch data access policy
    const provider = this.createOpenSearchPolicyLambda();

    const dataAccessPolicy = new cdk.CustomResource(this, 'OpenSearchDataAccessPolicy', {
      serviceToken: provider.serviceToken,
      properties: {
        PolicyName: 'BedrockOpenSearchDataAccess',
        PolicyType: 'data-access',
        CollectionId: props.collectionId,
        RoleName: props.roleName,
        Region: props.region,
      },
    });
  }

  /**
   * Creates a Lambda function to create the OpenSearch data access policy
   */
  private createOpenSearchPolicyLambda() {
    // Create a Lambda function to create the OpenSearch data access policy
    const fn = new cdk.aws_lambda.Function(this, 'OpenSearchPolicyLambda', {
      runtime: cdk.aws_lambda.Runtime.NODEJS_20_X,
      handler: 'index.handler',
      code: cdk.aws_lambda.Code.fromInline(`
        const AWS = require('aws-sdk');
        
        exports.handler = async (event) => {
          console.log('Event:', JSON.stringify(event, null, 2));
          
          const { RequestType, PhysicalResourceId } = event;
          const { PolicyName, PolicyType, CollectionId, RoleName, Region } = event.ResourceProperties;
          
          // Use a consistent physical ID
          const resourceId = PhysicalResourceId || \`OpenSearchPolicy-\${PolicyName}\`;
          
          if (RequestType === 'Delete') {
            console.log('Delete request - no action needed');
            return {
              PhysicalResourceId: resourceId
            };
          }
          
          if (RequestType === 'Create' || RequestType === 'Update') {
            try {
              const opensearchserverless = new AWS.OpenSearchServerless({ region: Region });
              
              // Create the data access policy
              const policy = {
                Rules: [
                  {
                    ResourceType: 'index',
                    Resource: [\`index/\${CollectionId}/*\`],
                    Permission: [
                      'aoss:CreateIndex',
                      'aoss:DeleteIndex',
                      'aoss:DescribeIndex',
                      'aoss:ReadDocument',
                      'aoss:WriteDocument',
                      'aoss:UpdateIndex'
                    ]
                  }
                ],
                Principal: [\`arn:aws:iam::\${process.env.AWS_ACCOUNT_ID}:role/\${RoleName}\`]
              };
              
              // Check if policy already exists
              try {
                await opensearchserverless.getSecurityPolicy({
                  name: PolicyName,
                  type: PolicyType
                }).promise();
                
                // Update existing policy
                await opensearchserverless.updateSecurityPolicy({
                  name: PolicyName,
                  type: PolicyType,
                  policy: JSON.stringify(policy)
                }).promise();
                
                console.log(\`Updated \${PolicyType} policy \${PolicyName}\`);
              } catch (e) {
                if (e.code === 'ResourceNotFoundException') {
                  // Create new policy
                  await opensearchserverless.createSecurityPolicy({
                    name: PolicyName,
                    type: PolicyType,
                    policy: JSON.stringify(policy)
                  }).promise();
                  
                  console.log(\`Created \${PolicyType} policy \${PolicyName}\`);
                } else {
                  throw e;
                }
              }
              
              return {
                PhysicalResourceId: resourceId,
                Data: {
                  PolicyName: PolicyName,
                  PolicyType: PolicyType
                }
              };
            } catch (error) {
              console.error('Error creating/updating OpenSearch policy:', error);
              throw error;
            }
          }
          
          throw new Error(\`Unsupported request type: \${RequestType}\`);
        }
      `),
      timeout: cdk.Duration.minutes(5),
      environment: {
        AWS_ACCOUNT_ID: cdk.Stack.of(this).account,
      },
    });

    // Add permissions to the Lambda function
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'opensearchserverless:CreateSecurityPolicy',
          'opensearchserverless:UpdateSecurityPolicy',
          'opensearchserverless:GetSecurityPolicy',
          'opensearchserverless:DeleteSecurityPolicy',
        ],
        resources: ['*'],
      })
    );

    // Create a provider for the custom resource
    return new cdk.custom_resources.Provider(this, 'OpenSearchPolicyProvider', {
      onEventHandler: fn,
    });
  }
}
