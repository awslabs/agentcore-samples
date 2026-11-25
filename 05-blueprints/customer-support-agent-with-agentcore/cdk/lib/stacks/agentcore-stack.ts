import * as cdk from 'aws-cdk-lib/core';
import { Construct } from 'constructs/lib/construct';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda'
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { BaseStackProps } from '../types';
import * as path from 'path';

export interface AgentCoreStackProps extends BaseStackProps {
    imageUri: string
}

export class AgentCoreStack extends cdk.Stack {
    readonly agentCoreRuntime: bedrockagentcore.CfnRuntime;
    readonly agentCoreGateway: bedrockagentcore.CfnGateway;
    readonly agentCoreMemory: bedrockagentcore.CfnMemory;
    readonly customerLambda: lambda.Function;
    readonly orderLambda: lambda.Function;

    constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
        super(scope, id, props);

        const region = cdk.Stack.of(this).region;
        const accountId = cdk.Stack.of(this).account;

        /*****************************
        * AgentCore Gateway
        ******************************/

        // Customer Domain Lambda
        this.customerLambda = new lambda.Function(this, `${props.appName}-CustomerLambda`, {
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "customer_handler.lambda_handler",
            code: lambda.AssetCode.fromAsset(path.join(__dirname, '../../../mcp/lambda'))
        });

        // Order Domain Lambda
        this.orderLambda = new lambda.Function(this, `${props.appName}-OrderLambda`, {
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "order_handler.lambda_handler",
            code: lambda.AssetCode.fromAsset(path.join(__dirname, '../../../mcp/lambda'))
        });

        const engine = new bedrockagentcore.CfnPolicyEngine(this, 'RefundPolicyEngine', {
            name: 'SupportRefundPolicy', description: 'Default-deny policies for synthetic refund requests'
        });
        const agentCoreGatewayRole = new iam.Role(this, `${props.appName}-AgentCoreGatewayRole`, {
            assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {conditions: {StringEquals: {'aws:SourceAccount': accountId}, ArnLike: {'aws:SourceArn': `arn:aws:bedrock-agentcore:${region}:${accountId}:gateway/*`}}}),
            description: 'IAM role for Bedrock AgentCore Gateway',
        });

        this.customerLambda.grantInvoke(agentCoreGatewayRole);
        this.orderLambda.grantInvoke(agentCoreGatewayRole);

        // Add Policy Engine permissions to Gateway role
        // Required for Policy Engine integration (per IAM Service Authorization Reference):
        // - GetPolicyEngine: retrieve policy engine
        // - AuthorizeAction: evaluate Cedar policies for authorization requests
        // - PartiallyAuthorizeActions: partial evaluation for listing allowed tools
        agentCoreGatewayRole.addToPolicy(new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                'bedrock-agentcore:GetPolicyEngine',
                'bedrock-agentcore:AuthorizeAction',
                'bedrock-agentcore:PartiallyAuthorizeActions',
            ],
            resources: [
                engine.attrPolicyEngineArn,
                `arn:aws:bedrock-agentcore:${region}:${accountId}:gateway/*`,
            ],
        }));

        // Create gateway resource
        // Cognito resources
        const cognitoUserPool = new cognito.UserPool(this, `${props.appName}-CognitoUserPool`, {
            selfSignUpEnabled: false,
        });

        // Cognito Groups for tier-based authorization
        // These automatically add 'cognito:groups' claim to access tokens
        new cognito.CfnUserPoolGroup(this, `${props.appName}-StandardGroup`, {
            userPoolId: cognitoUserPool.userPoolId,
            groupName: 'standard',
            description: 'Standard tier - max $100 refund',
            precedence: 3,
        });

        // Define granular scopes for Runtime and Gateway
        const runtimeInvokeScope = {
            scopeName: 'runtime:invoke',
            scopeDescription: 'Permission to invoke AgentCore Runtime',
        };

        const gatewayInvokeScope = {
            scopeName: 'gateway:invoke',
            scopeDescription: 'Permission to invoke AgentCore Gateway MCP tools',
        };

        const cognitoResourceServer = cognitoUserPool.addResourceServer(`${props.appName}-CognitoResourceServer`, {
            identifier: `${props.appName}-api`,
            scopes: [runtimeInvokeScope, gatewayInvokeScope],
        });

        // User App Client (for end-user authentication)
        const cognitoUserAppClient = new cognito.UserPoolClient(this, `${props.appName}-CognitoUserAppClient`, {
            userPool: cognitoUserPool,
            generateSecret: false, // Public client for SPA
            authFlows: {
                userPassword: true,  // Enable USER_PASSWORD_AUTH for CLI testing
                userSrp: true,       // Enable SRP auth
            },
            oAuth: {
                flows: {
                    authorizationCodeGrant: true,
                },
                scopes: [
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                    // Both scopes available - user requests what they need
                    cognito.OAuthScope.resourceServer(cognitoResourceServer, runtimeInvokeScope),
                    cognito.OAuthScope.resourceServer(cognitoResourceServer, gatewayInvokeScope),
                ],
                callbackUrls: ['http://localhost:3000/callback'],
                logoutUrls: ['http://localhost:3000/logout'],
            },
            supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO],
            accessTokenValidity: cdk.Duration.hours(1),
            idTokenValidity: cdk.Duration.hours(1),
            refreshTokenValidity: cdk.Duration.days(30),
            readAttributes: new cognito.ClientAttributes()
                .withStandardAttributes({ email: true, emailVerified: true }),
        });

        cognitoUserPool.addDomain(`${props.appName}-CognitoDomain`, {
            cognitoDomain: {
                domainPrefix: `${props.appName.toLowerCase()}-${cdk.Aws.ACCOUNT_ID}-${region}`,
            },
        });

        this.agentCoreGateway = new bedrockagentcore.CfnGateway(this, `${props.appName}-AgentCoreGateway`, {
            name: `${props.appName}-Gateway`,
            protocolType: "MCP",
            roleArn: agentCoreGatewayRole.roleArn,
            policyEngineConfiguration: { arn: engine.attrPolicyEngineArn, mode: "ENFORCE" },
            authorizerType: "CUSTOM_JWT",
            authorizerConfiguration: {
                customJwtAuthorizer: {
                    discoveryUrl: `https://cognito-idp.${region}.amazonaws.com/${cognitoUserPool.userPoolId}/.well-known/openid-configuration`,
                    allowedClients: [
                        cognitoUserAppClient.userPoolClientId,  // User-facing client only
                    ],
                    allowedScopes: [
                        `${props.appName}-api/${gatewayInvokeScope.scopeName}`,  // Required scope for Gateway
                    ],
                },
            },
        });

        // Customer Domain Target
        // Gateway creation validates GetPolicyEngine before any target exists.
        this.agentCoreGateway.node.addDependency(agentCoreGatewayRole);

        const customerTarget = new bedrockagentcore.CfnGatewayTarget(this, `${props.appName}-CustomerTarget`, {
            name: `${props.appName}-CustomerTarget`,
            gatewayIdentifier: this.agentCoreGateway.attrGatewayIdentifier,
            credentialProviderConfigurations: [
                {
                    credentialProviderType: "GATEWAY_IAM_ROLE",
                },
            ],
            targetConfiguration: {
                mcp: {
                    lambda: {
                        lambdaArn: this.customerLambda.functionArn,
                        toolSchema: {
                            inlinePayload: [
                                {
                                    name: "get_customer",
                                    description: "Look up customer information by customer ID. Returns customer details including name, email, and member since date.",
                                    inputSchema: {
                                        type: "object",
                                        properties: {
                                            customer_id: { type: 'string', description: 'The customer ID to look up (e.g., CUST-001)' }
                                        },
                                        required: ['customer_id']
                                    }
                                },
                                {
                                    name: "list_customers",
                                    description: "List all customers. Returns customer details including customer ID, name, email, and member since date.",
                                    inputSchema: {
                                        type: "object",
                                        properties: {
                                            name: { type: 'string', description: 'Optional filter to search customers by name (case-insensitive partial match)' }
                                        },
                                        required: []
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        });

        // Order Domain Target
        const orderTarget = new bedrockagentcore.CfnGatewayTarget(this, `${props.appName}-OrderTarget`, {
            name: `${props.appName}-OrderTarget`,
            gatewayIdentifier: this.agentCoreGateway.attrGatewayIdentifier,
            credentialProviderConfigurations: [
                {
                    credentialProviderType: "GATEWAY_IAM_ROLE",
                },
            ],
            targetConfiguration: {
                mcp: {
                    lambda: {
                        lambdaArn: this.orderLambda.functionArn,
                        toolSchema: {
                            inlinePayload: [
                                {
                                    name: "get_order",
                                    description: "Look up order details by order ID. Returns order information including items, status, total, and delivery date.",
                                    inputSchema: {
                                        type: "object",
                                        properties: {
                                            order_id: { type: 'string', description: 'The order ID to look up (e.g., ORD-12345)' }
                                        },
                                        required: ['order_id']
                                    }
                                },
                                {
                                    name: "list_orders",
                                    description: "List orders for a customer. Returns a list of orders with order ID, total, status, and order date.",
                                    inputSchema: {
                                        type: "object",
                                        properties: {
                                            customer_id: { type: 'string', description: 'The customer ID to list orders for (e.g., CUST-001)' },
                                            limit: { type: 'integer', description: 'Maximum number of orders to return (default: 10)' }
                                        },
                                        required: ['customer_id']
                                    }
                                },
                                {
                                    name: "process_refund",
                                    description: "Process a refund for an order. Requires order ID, refund amount, and reason. Returns refund confirmation with refund ID.",
                                    inputSchema: {
                                        type: "object",
                                        properties: {
                                            order_id: { type: 'string', description: 'The order ID to refund (e.g., ORD-12345)' },
                                            amount_cents: { type: 'integer', description: 'Positive integer US cents (10000 = $100)' },
                                            reason: { type: 'string', description: 'The reason for the refund (e.g., damaged_item, wrong_item, customer_request)' }
                                        },
                                        required: ['order_id', 'amount_cents', 'reason']
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        });

        // Ensure GatewayTargets wait for the IAM policy (created by grantInvoke) to be attached to the role
        customerTarget.node.addDependency(agentCoreGatewayRole);
        orderTarget.node.addDependency(agentCoreGatewayRole);

        // The Gateway is ENFORCE from creation. Until validated permits exist, access is denied.
        const resource = `AgentCore::Gateway::"${this.agentCoreGateway.attrGatewayArn}"`;
        const readActions = ['CustomerTarget___get_customer','CustomerTarget___list_customers','OrderTarget___get_order','OrderTarget___list_orders']
            .map(action => `AgentCore::Action::"${props.appName}-${action}"`).join(', ');
        const readPolicy = new bedrockagentcore.CfnPolicy(this, 'ReadDemoRecords', {
            name: 'ReadDemoRecords', policyEngineId: engine.attrPolicyEngineId,
            definition: { cedar: { statement: `permit(principal, action in [${readActions}], resource == ${resource});` } }
        });
        const refundPolicy = new bedrockagentcore.CfnPolicy(this, 'BoundedRefund', {
            name: 'BoundedRefund', policyEngineId: engine.attrPolicyEngineId,
            definition: { cedar: { statement: `permit(principal, action == AgentCore::Action::"${props.appName}-OrderTarget___process_refund", resource == ${resource}) when { principal.hasTag("cognito:groups") && principal.getTag("cognito:groups") == "[\\\"standard\\\"]" && context.input.amount_cents > 0 && context.input.amount_cents <= 10000 };` } }
        });
        for (const policy of [readPolicy, refundPolicy]) { policy.addDependency(customerTarget); policy.addDependency(orderTarget); }

        
        /*****************************
        * AgentCore Memory
        ******************************/

        this.agentCoreMemory = new bedrockagentcore.CfnMemory(this, `${props.appName}-AgentCoreMemoryV2`, {
            name: "supportAgentDemo_Memory_v2",
            eventExpiryDuration: 30,
            description: "Memory resource with all built-in strategies",
            memoryStrategies: [
                {
                    semanticMemoryStrategy: {
                        name: "FactExtractor",
                        namespaces: ["/facts/{actorId}/"],
                    }
                },
                {
                    userPreferenceMemoryStrategy: {
                        name: "PreferenceLearner",
                        namespaces: ["/preferences/{actorId}/"],
                    }
                },
                {
                    summaryMemoryStrategy: {
                        name: "SessionSummarizer",
                        namespaces: ["/summaries/{actorId}/{sessionId}/"],
                    }
                },
                {
                    episodicMemoryStrategy: {
                        name: "EpisodeTracker",
                        namespaces: ["/episodes/{actorId}/{sessionId}/"],
                        reflectionConfiguration: {
                            namespaces: ["/episodes/{actorId}/"],
                        }
                    }
                }
            ],
        });
        
        /*****************************
        * AgentCore Runtime
        ******************************/

        // taken from https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html#runtime-permissions-execution
        const runtimePolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    sid: 'ECRImageAccess',
                    effect: iam.Effect.ALLOW,
                    actions: ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer'],
                    resources: [
                        `arn:aws:ecr:${region}:${accountId}:repository/cdk-hnb659fds-container-assets-${accountId}-${region}`,
                    ],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ['logs:DescribeLogStreams', 'logs:CreateLogGroup'],
                    resources: [
                        `arn:aws:logs:${region}:${accountId}:log-group:/aws/bedrock-agentcore/runtimes/*`,
                    ],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ['logs:DescribeLogGroups'],
                    resources: [
                        `arn:aws:logs:${region}:${accountId}:log-group:*`,
                    ],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
                    resources: [
                        `arn:aws:logs:${region}:${accountId}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`,
                    ],
                }),
                new iam.PolicyStatement({
                    sid: 'ECRTokenAccess',
                    effect: iam.Effect.ALLOW,
                    actions: ['ecr:GetAuthorizationToken'],
                    resources: ['*'],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: [
                        'xray:PutTraceSegments',
                        'xray:PutTelemetryRecords',
                        'xray:GetSamplingRules',
                        'xray:GetSamplingTargets',
                    ],
                resources: ['*'],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ['cloudwatch:PutMetricData'],
                    resources: ['*'],
                    conditions: {
                        StringEquals: { 'cloudwatch:namespace': 'bedrock-agentcore' },
                    },
                }),
                new iam.PolicyStatement({
                    sid: 'GetAgentAccessToken',
                    effect: iam.Effect.ALLOW,
                    actions: [
                        'bedrock-agentcore:GetWorkloadAccessToken',
                        'bedrock-agentcore:GetWorkloadAccessTokenForJWT',
                        'bedrock-agentcore:GetWorkloadAccessTokenForUserId',
                    ],
                    resources: [
                        `arn:aws:bedrock-agentcore:${region}:${accountId}:workload-identity-directory/default`,
                        `arn:aws:bedrock-agentcore:${region}:${accountId}:workload-identity-directory/default/workload-identity/agentName-*`,
                    ],
                }),
                new iam.PolicyStatement({
                    sid: 'BedrockModelInvocation',
                    effect: iam.Effect.ALLOW,
                    actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
                    resources: [
                        `arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0`,
                        `arn:aws:bedrock:${region}:${accountId}:inference-profile/global.anthropic.claude-sonnet-4-5-20250929-v1:0`,
                    ],
                }),
                new iam.PolicyStatement({
                    sid: 'AgentCoreMemoryAccess',
                    effect: iam.Effect.ALLOW,
                    actions: [
                        'bedrock-agentcore:CreateEvent',
                        'bedrock-agentcore:ListEvents',
                        'bedrock-agentcore:GetMemory',
                        'bedrock-agentcore:RetrieveMemoryRecords',
                    ],
                    resources: [
                        this.agentCoreMemory.attrMemoryArn,
                    ],
                }),
            ],
        });

        const runtimeRole = new iam.Role(this, `${props.appName}-AgentCoreRuntimeRole`, {
            assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {conditions: {StringEquals: {'aws:SourceAccount': accountId}, ArnLike: {'aws:SourceArn': `arn:aws:bedrock-agentcore:${region}:${accountId}:runtime/*`}}}),
            description: 'IAM role for Bedrock AgentCore Runtime',
            inlinePolicies: {
                RuntimeAccessPolicy: runtimePolicy
            }
        });

        // Ensure role policy waits for memory resource ARN
        runtimeRole.node.addDependency(this.agentCoreMemory);

        this.agentCoreRuntime = new bedrockagentcore.CfnRuntime(this, `${props.appName}-AgentCoreRuntime`, {
            agentRuntimeArtifact: {
                containerConfiguration: {
                    containerUri: props.imageUri
                }
            },
            agentRuntimeName: "supportAgentDemo_Agent",
            protocolConfiguration: "HTTP",
            networkConfiguration: {
                networkMode: "PUBLIC"
            },
            roleArn: runtimeRole.roleArn,

            // JWT Authorizer Configuration
            authorizerConfiguration: {
                customJwtAuthorizer: {
                    discoveryUrl: `https://cognito-idp.${region}.amazonaws.com/${cognitoUserPool.userPoolId}/.well-known/openid-configuration`,
                    allowedClients: [cognitoUserAppClient.userPoolClientId],
                    allowedScopes: [`${props.appName}-api/${runtimeInvokeScope.scopeName}`],
                },
            },

            // Pass Authorization header to container
            requestHeaderConfiguration: {
                requestHeaderAllowlist: ['Authorization'],
            },

            environmentVariables: {
                "AWS_REGION": region,
                "GATEWAY_URL": this.agentCoreGateway.attrGatewayUrl,
                "BEDROCK_AGENTCORE_MEMORY_ID": this.agentCoreMemory.attrMemoryId,
            }
        });

        // DEFAULT endpoint always points to newest published version. Optionally, can use these versioned endpoints below
        // https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agent-runtime-versioning.html
        void new bedrockagentcore.CfnRuntimeEndpoint(this, `${props.appName}-AgentCoreRuntimeProdEndpoint`, {
            agentRuntimeId: this.agentCoreRuntime.attrAgentRuntimeId,
            agentRuntimeVersion: "1",
            name: "PROD"
        });

        void new bedrockagentcore.CfnRuntimeEndpoint(this, `${props.appName}-AgentCoreRuntimeDevEndpoint`, {
            agentRuntimeId: this.agentCoreRuntime.attrAgentRuntimeId,
            agentRuntimeVersion: "1",
            name: "DEV"
        });

        /*****************************
        * CfnOutputs — consumed by scripts/deploy.sh via --outputs-file
        ******************************/

        new cdk.CfnOutput(this, 'UserPoolId', { value: cognitoUserPool.userPoolId });
        new cdk.CfnOutput(this, 'ClientId', { value: cognitoUserAppClient.userPoolClientId });
        new cdk.CfnOutput(this, 'CognitoDomain', {
            value: `${props.appName.toLowerCase()}-${accountId}-${region}.auth.${region}.amazoncognito.com`,
        });
        new cdk.CfnOutput(this, 'Region', { value: region });
        new cdk.CfnOutput(this, 'AccountId', { value: accountId });
        new cdk.CfnOutput(this, 'RuntimeArn', { value: this.agentCoreRuntime.attrAgentRuntimeArn });
        new cdk.CfnOutput(this, 'RuntimeId', { value: this.agentCoreRuntime.attrAgentRuntimeId });
        new cdk.CfnOutput(this, 'RuntimeName', { value: 'supportAgentDemo_Agent' });
        new cdk.CfnOutput(this, 'GatewayUrl', { value: this.agentCoreGateway.attrGatewayUrl });
        new cdk.CfnOutput(this, 'GatewayId', { value: this.agentCoreGateway.attrGatewayIdentifier });
        new cdk.CfnOutput(this, 'MemoryId', { value: this.agentCoreMemory.attrMemoryId });
        new cdk.CfnOutput(this, 'MemoryArn', { value: this.agentCoreMemory.attrMemoryArn });
        new cdk.CfnOutput(this, 'AuthorizerDiscoveryUrl', {
            value: `https://cognito-idp.${region}.amazonaws.com/${cognitoUserPool.userPoolId}/.well-known/openid-configuration`,
        });
    }
}