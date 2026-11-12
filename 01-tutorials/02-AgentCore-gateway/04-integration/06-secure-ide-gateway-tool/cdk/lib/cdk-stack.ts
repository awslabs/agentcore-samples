import * as cdk from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as integrations from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import * as path from "path";
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha";

export class CdkStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // =============================================================================
    // COGNITO USER POOL
    // =============================================================================

    // Create Cognito User Pool
    const userPool = new cognito.UserPool(this, "AgentCoreConfluencePool", {
      //userPoolName: `agentcore-confluence-pool`,
      selfSignUpEnabled: false,
      signInAliases: {
        email: true,
      },
      autoVerify: {
        email: true,
      },
      standardAttributes: {
        email: {
          required: true,
          mutable: true,
        },
      },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Create Cognito Domain
    const cognitoDomainPrefix = "agentcore-figma";
    const cognitoDomain = userPool.addDomain("CognitoDomain", {
      cognitoDomain: {
        domainPrefix: cognitoDomainPrefix,
      },
    });

    const readScope = new cognito.ResourceServerScope({
      scopeName: "mcp.read",
      scopeDescription: "Read MCP",
    });
    const writeScope = new cognito.ResourceServerScope({
      scopeName: "mcp.write",
      scopeDescription: "Write MCP",
    });
    // Create Resource Server
    const resourceServer = userPool.addResourceServer(
      "AgentCoreResourceServer",
      {
        identifier: "agentcore-gateway",
        userPoolResourceServerName: "AgentCore Gateway",
        scopes: [readScope, writeScope],
      },
    );

    const mcpScopes = [
      cognito.OAuthScope.resourceServer(resourceServer, readScope),
      cognito.OAuthScope.resourceServer(resourceServer, writeScope),
    ];

    // =============================================================================
    // DYNAMODB AUTH CODE TABLE
    // =============================================================================

    const authCodeTable = new dynamodb.Table(this, "AuthCodeTable", {
      partitionKey: { name: "code", type: dynamodb.AttributeType.STRING },
      timeToLiveAttribute: "ttl",
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // M2M Client for testing
    const m2mClient = userPool.addClient("M2MClient", {
      userPoolClientName: `agentcore-m2m`,
      generateSecret: true,
      oAuth: {
        flows: {
          clientCredentials: true,
        },
        scopes: mcpScopes,
      },
    });

    // Create Lambda execution role
    const callbackLambdaRole = new iam.Role(this, "CallbackLambdaRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSLambdaBasicExecutionRole",
        ),
      ],
    });

    // CompleteResourceTokenAuth for 3LO OAuth callback (requires Secrets Manager and KMS
    // to access the stored OAuth credentials during token completion)
    callbackLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock-agentcore:CompleteResourceTokenAuth",
          "secretsmanager:GetSecretValue",
          "kms:Decrypt",
        ],
        resources: ["*"],
      }),
    );

    // Token refresh: the callback Lambda validates cookies itself and may
    // need to refresh an expired access_token via Cognito
    callbackLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["cognito-idp:InitiateAuth"],
        resources: [userPool.userPoolArn],
      }),
    );

    // Callback Lambda (with bundled boto3 for AgentCore APIs)
    const callbackLambda = new lambda.Function(this, "McpCallbackLambda", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda_function.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../lambda"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            "bash",
            "-c",
            [
              "pip install --target /asset-output boto3 botocore 'python-jose[cryptography]' --upgrade",
              "cp callback_lambda.py /asset-output/lambda_function.py",
            ].join(" && "),
          ],
        },
      }),
      role: callbackLambdaRole,
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        AUTH_CODE_TABLE: authCodeTable.tableName,
        USER_POOL_ID: userPool.userPoolId,
        CLIENT_ID: "", // Updated below after VS Code client creation
      },
    });

    // =============================================================================
    // IDP LAMBDA (OAuth endpoints: login, token, authorize, register, metadata)
    // =============================================================================

    const idpLambdaRole = new iam.Role(this, "IdpLambdaRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSLambdaBasicExecutionRole",
        ),
      ],
    });

    idpLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "cognito-idp:InitiateAuth",
          "cognito-idp:RespondToAuthChallenge",
        ],
        resources: [userPool.userPoolArn],
      }),
    );

    authCodeTable.grantReadWriteData(idpLambdaRole);
    authCodeTable.grantReadWriteData(callbackLambdaRole);

    const idpLambda = new lambda.Function(this, "IdpLambda", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda_function.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../lambda"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            "bash",
            "-c",
            ["cp idp_lambda.py /asset-output/lambda_function.py"].join(
              " && ",
            ),
          ],
        },
      }),
      role: idpLambdaRole,
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        COGNITO_DOMAIN: `https://${cognitoDomain.domainName}.auth.${this.region}.amazoncognito.com`,
        CLIENT_ID: "", // Updated below after VS Code client creation
        CALLBACK_LAMBDA_URL: "", // Updated below after API Gateway creation
        AUTH_CODE_TABLE: authCodeTable.tableName,
        USER_POOL_ID: userPool.userPoolId,
      },
    });

    // =============================================================================
    // MCP PROXY LAMBDA (forwards MCP requests to AgentCore Gateway)
    // =============================================================================

    const mcpLambdaRole = new iam.Role(this, "McpProxyLambdaRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSLambdaBasicExecutionRole",
        ),
      ],
    });

    mcpLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["bedrock-agentcore:InvokeGateway"],
        resources: ["*"],
      }),
    );

    authCodeTable.grantReadWriteData(mcpLambdaRole);

    const mcpLambda = new lambda.Function(this, "McpProxyLambda", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda_function.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../lambda"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            "bash",
            "-c",
            ["cp mcp_lambda.py /asset-output/lambda_function.py"].join(
              " && ",
            ),
          ],
        },
      }),
      role: mcpLambdaRole,
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      environment: {
        GATEWAY_URL: "", // Updated below after gateway creation
        CALLBACK_LAMBDA_URL: "", // Updated below after API Gateway creation
        AUTH_CODE_TABLE: authCodeTable.tableName,
      },
    });

    // =============================================================================
    // API GATEWAY
    // =============================================================================

    // Create HTTP API
    const httpApi = new apigateway.HttpApi(this, "McpOAuthProxyApi", {
      //apiName: `mcp-oauth-proxy`,
    });

    const apiOrigin = `https://${httpApi.httpApiId}.execute-api.${this.region}.amazonaws.com`;

    httpApi.addStage("DefaultStageWithCors", {
      autoDeploy: true,
      stageName: "$default",
    });

    const cfnApi = httpApi.node.defaultChild as cdk.CfnResource;
    cfnApi.addPropertyOverride("CorsConfiguration", {
      AllowOrigins: [apiOrigin],
      AllowMethods: ["GET", "POST", "OPTIONS"],
      AllowHeaders: ["Content-Type", "Authorization", "MCP-Protocol-Version", "MCP-Session-Id"],
    });

    // Create Lambda integrations
    const idpIntegration = new integrations.HttpLambdaIntegration(
      "IdpIntegration",
      idpLambda,
    );

    const mcpIntegration = new integrations.HttpLambdaIntegration(
      "McpIntegration",
      mcpLambda,
    );

    const callbackIntegration = new integrations.HttpLambdaIntegration(
      "CallbackIntegration",
      callbackLambda,
    );

    // IDP routes (OAuth endpoints)
    httpApi.addRoutes({
      path: "/.well-known/oauth-authorization-server",
      methods: [apigateway.HttpMethod.GET],
      integration: idpIntegration,
    });

    httpApi.addRoutes({
      path: "/.well-known/oauth-protected-resource",
      methods: [apigateway.HttpMethod.GET],
      integration: idpIntegration,
    });

    httpApi.addRoutes({
      path: "/authorize",
      methods: [apigateway.HttpMethod.GET],
      integration: idpIntegration,
    });

    httpApi.addRoutes({
      path: "/token",
      methods: [apigateway.HttpMethod.POST],
      integration: idpIntegration,
    });

    httpApi.addRoutes({
      path: "/register",
      methods: [apigateway.HttpMethod.POST],
      integration: idpIntegration,
    });

    httpApi.addRoutes({
      path: "/login",
      methods: [apigateway.HttpMethod.POST],
      integration: idpIntegration,
    });

    // MCP proxy route
    httpApi.addRoutes({
      path: "/mcp",
      methods: [apigateway.HttpMethod.ANY],
      integration: mcpIntegration,
    });

    // Callback routes (3LO OAuth)
    httpApi.addRoutes({
      path: "/ping",
      methods: [apigateway.HttpMethod.GET],
      integration: callbackIntegration,
    });

    httpApi.addRoutes({
      path: "/oauth2/callback",
      methods: [apigateway.HttpMethod.POST],
      integration: callbackIntegration,
    });

    // Get API endpoint
    const apiEndpoint =
      httpApi.url ||
      `https://${httpApi.httpApiId}.execute-api.${this.region}.amazonaws.com`;

    // =============================================================================
    // VS CODE COGNITO CLIENT (with API Gateway callback)
    // =============================================================================

    const callbackUrls = [
      "http://127.0.0.1:33418",
      "http://127.0.0.1:33418/",
      "http://localhost:33418",
      "http://localhost:33418/",
      `${apiEndpoint}callback`,
      `${apiEndpoint}callback/`,
      "https://vscode.dev/redirect",
      "https://insiders.vscode.dev/redirect",
    ];

    const vscodeClient = userPool.addClient("VSCodeClient", {
      //userPoolClientName: `agentcore-vscode`,
      generateSecret: false,
      oAuth: {
        flows: {
          authorizationCodeGrant: true,
        },
        scopes: [
          cognito.OAuthScope.OPENID,
          cognito.OAuthScope.PROFILE,
          cognito.OAuthScope.EMAIL,
          cognito.OAuthScope.PHONE,
          ...mcpScopes,
        ],
        callbackUrls: callbackUrls,
      },
      authFlows: {
        userSrp: true,
        userPassword: true,
      },
      supportedIdentityProviders: [
        cognito.UserPoolClientIdentityProvider.COGNITO,
      ],
    });

    // /oauth2/callback — the 3LO provider redirects here after user consent.
    // The Lambda handles auth itself: it reads the access_token cookie,
    // verifies JWT signature, refreshes if needed, then calls
    // CompleteResourceTokenAuth server-side. If no valid session exists,
    // it redirects to /authorize with a return_to parameter.
    httpApi.addRoutes({
      path: "/oauth2/callback",
      methods: [apigateway.HttpMethod.GET],
      integration: callbackIntegration,
    });

    // Update Lambda environment variables with VS Code client ID and API endpoint
    idpLambda.addEnvironment("CLIENT_ID", vscodeClient.userPoolClientId);
    callbackLambda.addEnvironment("CLIENT_ID", vscodeClient.userPoolClientId);
    const callbackUrl = apiEndpoint.replace(/\/$/, "");
    idpLambda.addEnvironment("CALLBACK_LAMBDA_URL", callbackUrl);
    mcpLambda.addEnvironment("CALLBACK_LAMBDA_URL", callbackUrl);

    const gatewayRole = new iam.Role(this, "GatewayRole", {
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName("CloudWatchLogsFullAccess"),
      ],
      inlinePolicies: {
        agentcore: new iam.PolicyDocument({
          statements: [
            // Workload access token generation
            new iam.PolicyStatement({
              actions: [
                "bedrock-agentcore:GetWorkloadAccessToken",
                "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
              ],
              resources: [
                `arn:aws:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default`,
                `arn:aws:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/*`,
              ],
            }),
            new iam.PolicyStatement({
              actions: ["kms:*"],
              resources: ["arn:aws:kms:*:*:key/*"],
            }),
            // Token Vault — agent fetches OAuth tokens
            new iam.PolicyStatement({
              actions: [
                "bedrock-agentcore:GetResourceOauth2Token",
                "bedrock-agentcore:CompleteResourceTokenAuth",
                "secretsmanager:GetSecretValue",
              ],
              resources: ["*"],
            }),
            // Service-linked role creation for Runtime Identity
            new iam.PolicyStatement({
              actions: ["iam:CreateServiceLinkedRole"],
              resources: [
                `arn:aws:iam::${this.account}:role/aws-service-role/runtime-identity.bedrock-agentcore.amazonaws.com/*`,
              ],
              conditions: {
                StringEquals: {
                  "iam:AWSServiceName":
                    "runtime-identity.bedrock-agentcore.amazonaws.com",
                },
              },
            }),
          ],
        }),
      },
    });

    const gateway = new agentcore.Gateway(this, "AgentCoreMcpGateway", {
      gatewayName: `agentcore-figma-gateway`,
      description: "AgentCore Gateway for VS Code IDE integration",
      protocolConfiguration: agentcore.GatewayProtocol.mcp({
        searchType: agentcore.McpGatewaySearchType.SEMANTIC,
        supportedVersions: [
          agentcore.MCPProtocolVersion.MCP_2025_03_26,
          agentcore.MCPProtocolVersion.MCP_2025_06_18,
          "2025-11-25" as agentcore.MCPProtocolVersion,
        ],
      }),
      role: gatewayRole,
      exceptionLevel: agentcore.GatewayExceptionLevel.DEBUG,
      authorizerConfiguration: agentcore.GatewayAuthorizer.usingCognito({
        userPool: userPool,
        allowedClients: [vscodeClient],
      }),
      // This is not supported for now for 3LO outbound auth
      // authorizerConfiguration: agentcore.GatewayAuthorizer.usingAwsIam(),
    });

    mcpLambda.addEnvironment("GATEWAY_URL", gateway.gatewayUrl ?? "");

    // =============================================================================
    // OUTPUTS
    // =============================================================================

    new cdk.CfnOutput(this, "UserPoolId", {
      value: userPool.userPoolId,
      description: "Cognito User Pool ID",
    });

    new cdk.CfnOutput(this, "UserPoolArn", {
      value: userPool.userPoolArn,
      description: "Cognito User Pool ARN",
    });

    new cdk.CfnOutput(this, "CognitoDomain", {
      value: cognitoDomain.domainName,
      description: "Cognito Domain",
    });

    new cdk.CfnOutput(this, "CognitoDomainUrl", {
      value: `https://${cognitoDomain.domainName}.auth.${this.region}.amazoncognito.com`,
      description: "Cognito Domain URL",
    });

    new cdk.CfnOutput(this, "DiscoveryUrl", {
      value: `https://cognito-idp.${this.region}.amazonaws.com/${userPool.userPoolId}/.well-known/openid-configuration`,
      description: "OIDC Discovery URL",
    });

    new cdk.CfnOutput(this, "M2MClientId", {
      value: m2mClient.userPoolClientId,
      description: "M2M Client ID (for testing)",
    });

    new cdk.CfnOutput(this, "VSCodeClientId", {
      value: vscodeClient.userPoolClientId,
      description: "VS Code Client ID",
    });

    new cdk.CfnOutput(this, "ApiEndpoint", {
      value: apiEndpoint,
      description: "API Gateway Endpoint",
    });

    new cdk.CfnOutput(this, "IdpLambdaName", {
      value: idpLambda.functionName,
      description: "IDP Lambda Function Name",
    });

    new cdk.CfnOutput(this, "McpLambdaName", {
      value: mcpLambda.functionName,
      description: "MCP Proxy Lambda Function Name",
    });

    new cdk.CfnOutput(this, "CallbackLambdaName", {
      value: callbackLambda.functionName,
      description: "Callback Lambda Function Name",
    });

    new cdk.CfnOutput(this, "VSCodeMcpConfig", {
      value: JSON.stringify(
        {
          servers: {
            [`agentcore-confluence`]: {
              type: "http",
              url: apiEndpoint.replace(/\/$/, "") + "/mcp",
              headers: {
                "MCP-Protocol-Version": "2025-11-25",
              },
            },
          },
        },
        null,
        2,
      ),
      description: "VS Code MCP Configuration (add to .vscode/mcp.json)",
    });

    new cdk.CfnOutput(this, "Gateway", {
      value: gateway.gatewayId,
      description: "Gateway ID",
    });
  }
}
