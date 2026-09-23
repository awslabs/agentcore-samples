// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as apigwv2integrations from "aws-cdk-lib/aws-apigatewayv2-integrations";
import { Construct } from "constructs";
import * as path from "path";
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha";

export class CdkEntraIdStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // =========================================================================
    // All EntraID + OAuth config comes from CDK context (set by setup script
    // or -c flags). This allows multiple independent deployments.
    // =========================================================================
    const entraConfig = {
      tenantId: this.requireContext("entra:tenantId"),
      appAClientId: this.requireContext("entra:appAClientId"),
      appBClientId: this.requireContext("entra:appBClientId"),
      // "ciam" or "standard" — determines discovery/authority URLs
      tenantType: (this.node.tryGetContext("entra:tenantType") as string) || "standard",
      // Only needed for CIAM tenants (e.g. "your-domain")
      ciamDomain: (this.node.tryGetContext("entra:ciamDomain") as string) || "",
      // Pre-created via CLI
      oauthProviderArn: this.requireContext("oauth:providerArn"),
      oauthSecretArn: this.requireContext("oauth:secretArn"),
      oauthCallbackUrl: this.requireContext("oauth:callbackUrl"),
      // Credential provider name (for display in SPA)
      oauthProviderName: (this.node.tryGetContext("oauth:providerName") as string) || "entraid-weather-3lo",
    };

    // Derive URLs based on tenant type
    const isCiam = entraConfig.tenantType === "ciam";
    const authorityHost = isCiam
      ? `${entraConfig.ciamDomain}.ciamlogin.com`
      : "login.microsoftonline.com";
    const issuerHost = isCiam
      ? `${entraConfig.tenantId}.ciamlogin.com`
      : "login.microsoftonline.com";

    const discoveryUrl = `https://${authorityHost}/${entraConfig.tenantId}/v2.0/.well-known/openid-configuration`;
    const weatherScope = `api://${entraConfig.appBClientId}/weather.read`;
    const authority = `https://${authorityHost}/${entraConfig.tenantId}`;
    const issuer = `https://${issuerHost}/${entraConfig.tenantId}/v2.0`;

    // Unique suffix for resource names (avoids collisions between deployments)
    const suffix = (this.node.tryGetContext("resourceSuffix") as string) || "";
    const nameSuffix = suffix ? `-${suffix}` : "";

    // =========================================================================
    // IAM OIDC IDENTITY PROVIDER (EntraID → STS AssumeRoleWithWebIdentity)
    // =========================================================================
    // IAM OIDC providers are unique per issuer URL per account. If deploying
    // multiple stacks for the same tenant, pass the existing provider ARN.
    const existingOidcArn = this.node.tryGetContext("oidc:providerArn") as string;
    const oidcProvider = existingOidcArn
      ? iam.OpenIdConnectProvider.fromOpenIdConnectProviderArn(
          this,
          "EntraIdOidcProvider",
          existingOidcArn
        )
      : new iam.OpenIdConnectProvider(this, "EntraIdOidcProvider", {
          url: issuer,
          clientIds: [entraConfig.appAClientId],
        });

    const authOnboardingRole = new iam.Role(this, "AuthOnboardingWebRole", {
      roleName: `auth-onboarding-web-role${nameSuffix}`,
      assumedBy: new iam.WebIdentityPrincipal(
        oidcProvider.openIdConnectProviderArn,
        {
          StringEquals: {
            [`${issuerHost}/${entraConfig.tenantId}/v2.0:aud`]:
              entraConfig.appAClientId,
          },
        }
      ),
      maxSessionDuration: cdk.Duration.hours(1),
      inlinePolicies: {
        agentcoreIdentity: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: [
                "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                "bedrock-agentcore:GetResourceOauth2Token",
                "bedrock-agentcore:CompleteResourceTokenAuth",
              ],
              resources: [
                `arn:aws:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default`,
                `arn:aws:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/*`,
                `arn:aws:bedrock-agentcore:${this.region}:${this.account}:token-vault/default`,
                `arn:aws:bedrock-agentcore:${this.region}:${this.account}:token-vault/default/oauth2credentialprovider/*`,
              ],
            }),
            new iam.PolicyStatement({
              actions: ["secretsmanager:GetSecretValue"],
              resources: [
                `arn:aws:secretsmanager:${this.region}:${this.account}:secret:bedrock-agentcore-identity!default/oauth2/*`,
              ],
              conditions: {
                "ForAnyValue:StringEquals": {
                  "aws:CalledVia": ["bedrock-agentcore.amazonaws.com"],
                },
              },
            }),
          ],
        }),
      },
    });

    // =========================================================================
    // LAMBDA FUNCTIONS
    // =========================================================================
    const lambdaRole = new iam.Role(this, "McpProxyLambdaRole", {
      roleName: `mcp-proxy-entraid-lambda-role${nameSuffix}`,
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSLambdaBasicExecutionRole"
        ),
      ],
    });

    lambdaRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["bedrock-agentcore:InvokeGateway"],
        // Scoped to all gateways in this account/region. The gateway ARN is not
        // available at this point in the stack — it's created later. For production,
        // use a Lazy value or addToPolicy after gateway creation.
        resources: [
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:gateway/*`,
        ],
      })
    );

    lambdaRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["secretsmanager:GetSecretValue"],
        resources: [entraConfig.oauthSecretArn],
      })
    );

    // Elicitation interceptor Lambda
    const elicitationInterceptorLambda = new lambda.Function(
      this,
      "ElicitationInterceptor",
      {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: "elicitation_interceptor.lambda_handler",
        code: lambda.Code.fromAsset(
          path.join(__dirname, "../lambda"),
          {
            bundling: {
              image: lambda.Runtime.PYTHON_3_12.bundlingImage,
              command: [
                "bash",
                "-c",
                "cp elicitation_interceptor.py /asset-output/elicitation_interceptor.py",
              ],
              local: {
                tryBundle(outputDir: string) {
                  const fs = require("fs");
                  fs.copyFileSync(
                    path.join(
                      __dirname,
                      "../lambda/elicitation_interceptor.py"
                    ),
                    path.join(outputDir, "elicitation_interceptor.py")
                  );
                  return true;
                },
              },
            },
          }
        ),
        timeout: cdk.Duration.seconds(10),
        memorySize: 128,
        environment: {
          AUTH_ONBOARDING_URL: "", // set after API Gateway creation
        },
      }
    );

    // Proxy Lambda
    const proxyLambda = new lambda.Function(this, "McpProxyLambda", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda_function.lambda_handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "../lambda"),
        {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            command: [
              "bash",
              "-c",
              "pip install boto3 -t /asset-output && cp mcp_proxy_lambda.py /asset-output/lambda_function.py",
            ],
            local: {
              tryBundle(outputDir: string) {
                const fs = require("fs");
                const { execSync } = require("child_process");
                fs.copyFileSync(
                  path.join(__dirname, "../lambda/mcp_proxy_lambda.py"),
                  path.join(outputDir, "lambda_function.py")
                );
                execSync(`pip install boto3 -t "${outputDir}" --quiet`);
                return true;
              },
            },
          },
        }
      ),
      role: lambdaRole,
      timeout: cdk.Duration.seconds(29),
      memorySize: 256,
      environment: {
        GATEWAY_URL: "",
        ENTRA_TENANT_ID: entraConfig.tenantId,
        ENTRA_APP_A_CLIENT_ID: entraConfig.appAClientId,
        ENTRA_DISCOVERY_URL: discoveryUrl,
        CALLBACK_LAMBDA_URL: "",
        AUTH_ONBOARDING_ROLE_ARN: authOnboardingRole.roleArn,
        OAUTH_CREDENTIAL_PROVIDER_NAME: entraConfig.oauthProviderName,
        ENTRA_WEATHER_SCOPE: weatherScope,
        // Authority URLs — Lambda uses these for authorize/token endpoints
        ENTRA_AUTHORITY: authority,
        ENTRA_AUTHORITY_HOST: authorityHost,
        ENTRA_TENANT_TYPE: entraConfig.tenantType,
      },
    });

    // Weather REST API Lambda (only needs basic execution role — no AWS API calls)
    const weatherApiLambda = new lambda.Function(this, "WeatherApiLambda", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda_function.lambda_handler",
      code: lambda.Code.fromAsset(
        path.join(__dirname, "../lambda"),
        {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            command: [
              "bash",
              "-c",
              "cp weather_api_lambda.py /asset-output/lambda_function.py",
            ],
            local: {
              tryBundle(outputDir: string) {
                const fs = require("fs");
                fs.copyFileSync(
                  path.join(__dirname, "../lambda/weather_api_lambda.py"),
                  path.join(outputDir, "lambda_function.py")
                );
                return true;
              },
            },
          },
        }
      ),
      timeout: cdk.Duration.seconds(10),
      memorySize: 128,
    });

    // =========================================================================
    // API GATEWAY HTTP API
    // =========================================================================
    const httpApi = new apigwv2.HttpApi(this, "McpProxyApi", {
      apiName: `mcp-entraid-proxy-api${nameSuffix}`,
      description: "MCP OAuth Proxy with EntraID - API Gateway HTTP API",
    });

    const proxyIntegration = new apigwv2integrations.HttpLambdaIntegration(
      "ProxyIntegration",
      proxyLambda,
      { payloadFormatVersion: apigwv2.PayloadFormatVersion.VERSION_1_0 }
    );

    const weatherIntegration = new apigwv2integrations.HttpLambdaIntegration(
      "WeatherIntegration",
      weatherApiLambda,
      { payloadFormatVersion: apigwv2.PayloadFormatVersion.VERSION_1_0 }
    );

    httpApi.addRoutes({
      path: "/weather",
      methods: [apigwv2.HttpMethod.GET],
      integration: weatherIntegration,
    });

    httpApi.addRoutes({
      path: "/auth",
      methods: [apigwv2.HttpMethod.GET],
      integration: proxyIntegration,
    });
    httpApi.addRoutes({
      path: "/auth/callback",
      methods: [apigwv2.HttpMethod.GET],
      integration: proxyIntegration,
    });
    httpApi.addRoutes({
      path: "/{proxy+}",
      methods: [apigwv2.HttpMethod.ANY],
      integration: proxyIntegration,
    });
    httpApi.addRoutes({
      path: "/",
      methods: [apigwv2.HttpMethod.ANY],
      integration: proxyIntegration,
    });

    const apiEndpoint = httpApi.apiEndpoint;

    proxyLambda.addEnvironment("CALLBACK_LAMBDA_URL", apiEndpoint);
    elicitationInterceptorLambda.addEnvironment(
      "AUTH_ONBOARDING_URL",
      cdk.Fn.join("", [apiEndpoint, "/auth"])
    );

    // =========================================================================
    // AGENTCORE GATEWAY
    // =========================================================================
    const gatewayRole = new iam.Role(this, "GatewayRole", {
      assumedBy: iam.ServicePrincipal.fromStaticServicePrincipleName(
        "bedrock-agentcore.amazonaws.com"
      ),
      inlinePolicies: {
        getAccessToken: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: [
                "bedrock-agentcore:GetWorkloadAccess*",
                "bedrock-agentcore:GetResourceOauth2Token",
              ],
              resources: ["*"],
              effect: iam.Effect.ALLOW,
            }),
            new iam.PolicyStatement({
              actions: ["secretsmanager:GetSecretValue"],
              resources: ["*"],
              effect: iam.Effect.ALLOW,
              conditions: {
                "ForAnyValue:StringEquals": {
                  "aws:CalledVia": ["bedrock-agentcore.amazonaws.com"],
                },
              },
            }),
          ],
        }),
      },
    });

    const gateway = new agentcore.Gateway(this, "AgentCoreMcpGateway", {
      gatewayName: `agentcore-mcp-gateway-entraid${nameSuffix}`,
      description:
        "AgentCore Gateway with EntraID inbound + outbound 3LO auth",
      protocolConfiguration: agentcore.GatewayProtocol.mcp({
        searchType: agentcore.McpGatewaySearchType.SEMANTIC,
        supportedVersions: [
          agentcore.MCPProtocolVersion.MCP_2025_03_26,
          agentcore.MCPProtocolVersion.MCP_2025_06_18,
          "2025-11-25" as agentcore.MCPProtocolVersion,
        ],
      }),
      role: gatewayRole,
      // DEBUG exception level aids troubleshooting during development.
      // For production, use GatewayExceptionLevel.NONE or GatewayExceptionLevel.ERROR.
      exceptionLevel: agentcore.GatewayExceptionLevel.DEBUG,
      authorizerConfiguration: agentcore.GatewayAuthorizer.usingCustomJwt({
        discoveryUrl: discoveryUrl,
        allowedAudience: [entraConfig.appAClientId],
      }),
      interceptorConfigurations: [
        agentcore.LambdaInterceptor.forResponse(elicitationInterceptorLambda),
      ],
    });

    // =========================================================================
    // OPENAPI TARGET with OAuth 3LO
    // =========================================================================
    // OpenAPI spec — use deployment-specific file if provided, else default
    const openapiPath = (this.node.tryGetContext("openapi:path") as string)
      || path.join(__dirname, "../openapi/weather-api.json");
    const weatherApiSchema = agentcore.ApiSchema.fromLocalAsset(openapiPath);

    const weatherTarget = gateway.addOpenApiTarget("WeatherApiTarget", {
      gatewayTargetName: "weather-api",
      description: "Weather REST API with EntraID 3LO auth",
      apiSchema: weatherApiSchema,
      credentialProviderConfigurations: [
        agentcore.GatewayCredentialProvider.fromOauthIdentityArn({
          providerArn: entraConfig.oauthProviderArn,
          secretArn: entraConfig.oauthSecretArn,
          scopes: [weatherScope],
        }),
      ],
    });

    // Escape hatch: inject grantType and defaultReturnUrl
    const cfnTarget = weatherTarget.node.defaultChild as cdk.CfnResource;
    cfnTarget.addPropertyOverride(
      "CredentialProviderConfigurations.0.CredentialProvider.OauthCredentialProvider.GrantType",
      "AUTHORIZATION_CODE"
    );
    cfnTarget.addPropertyOverride(
      "CredentialProviderConfigurations.0.CredentialProvider.OauthCredentialProvider.DefaultReturnUrl",
      cdk.Fn.join("", [apiEndpoint, "/auth/callback"])
    );

    const rolePolicy = gatewayRole.node.tryFindChild("DefaultPolicy");
    if (rolePolicy) {
      cfnTarget.addDependency(rolePolicy.node.defaultChild as cdk.CfnResource);
    }

    proxyLambda.addEnvironment("GATEWAY_URL", gateway.gatewayUrl ?? "");

    // =========================================================================
    // OUTPUTS
    // =========================================================================
    new cdk.CfnOutput(this, "ApiEndpoint", {
      value: apiEndpoint,
      description: "API Gateway HTTP API Endpoint",
    });

    new cdk.CfnOutput(this, "GatewayId", {
      value: gateway.gatewayId,
    });

    new cdk.CfnOutput(this, "GatewayUrl", {
      value: gateway.gatewayUrl ?? "N/A",
    });

    new cdk.CfnOutput(this, "OAuthCallbackUrl", {
      value: entraConfig.oauthCallbackUrl,
      description: "Callback URL to register as redirect URI in EntraID App B",
    });

    new cdk.CfnOutput(this, "AuthOnboardingUrl", {
      value: cdk.Fn.join("", [apiEndpoint, "/auth"]),
      description: "URL for the auth onboarding web app",
    });

    new cdk.CfnOutput(this, "VSCodeMcpConfig", {
      value: cdk.Fn.join("", [
        '{"servers":{"agentcore-weather-entraid":{"type":"http","url":"',
        apiEndpoint,
        '/mcp","headers":{"MCP-Protocol-Version":"2025-11-25"}}}}',
      ]),
      description: "VS Code MCP Configuration (add to .vscode/mcp.json)",
    });
  }

  /** Read a required CDK context value or throw. */
  private requireContext(key: string): string {
    const value = this.node.tryGetContext(key) as string;
    if (!value) {
      throw new Error(
        `Missing required CDK context: -c ${key}=<value>`
      );
    }
    return value;
  }
}
