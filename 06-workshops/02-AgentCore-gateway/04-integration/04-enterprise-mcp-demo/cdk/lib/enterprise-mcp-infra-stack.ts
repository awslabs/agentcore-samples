import * as cdk from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as targets from "aws-cdk-lib/aws-elasticloadbalancingv2-targets";
import * as certificatemanager from "aws-cdk-lib/aws-certificatemanager";
import * as route53 from "aws-cdk-lib/aws-route53";
import * as route53targets from "aws-cdk-lib/aws-route53-targets";
import * as wafv2 from "aws-cdk-lib/aws-wafv2";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import * as path from "path";
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha";
import * as bedrockl1 from 'aws-cdk-lib/aws-bedrock';
import { AgentCorePolicyEngine } from "./agentcore-policy-engine";

export class EnterpriseMcpInfraStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // =============================================================================
    // SECURITY POSTURE – what this stack provides and what it does NOT
    // =============================================================================
    // PROVIDED:
    //   • Cognito User Pool (admin-only sign-up, MFA-ready, strong password policy)
    //     with a Pre-Token Generation Lambda that injects audience/role claims.
    //   • OAuth 2.0 Authorization Code Grant + custom scopes (mcp.read / mcp.write).
    //   • JWT audience validation in the proxy Lambda before any AgentCore call.
    //   • AgentCore Gateway Cognito authorizer (token verified a second time by AWS).
    //   • Cedar policy engine enforcing fine-grained per-user tool access (ENFORCE).
    //   • Bedrock Guardrails (PII masking/blocking) applied at the interceptor layer.
    //   • Lambda-in-VPC proxy (private subnet, NAT egress only).
    //   • VPC Interface Endpoint for bedrock-agentcore (InvokeGateway traffic stays
    //     on the AWS private network; never crosses the public internet).
    //   • Internet-facing ALB with:
    //       – TLS 1.2+ termination on a custom domain (ACM certificate).
    //       – dropInvalidHeaderFields (HTTP request-smuggling mitigation).
    //       – Host-header condition on every forwarding rule; raw *.elb DNS 404s.
    //       – HTTP → HTTPS permanent redirect on port 80.
    //   • WAF WebACL (Regional, attached to ALB):
    //       – IP rate limit (1 000 req / 5 min per IP)
    //       – AWS IP Reputation list (botnets, TOR exits, scanners)
    //       – Core Rule Set / OWASP Top 10
    //       – Known Bad Inputs
    //       – Bot Control – COMMON level (COUNT mode; switch to BLOCK post-validation)
    //   • Reserved Lambda concurrency caps on every function (DoS blast-radius limit).
    //   • Gateway resource policy restricting InvokeGateway to the VPC.
    //   • Shield Standard (automatically active on public ALBs, L3/L4 DDoS only).
    //   • ALB access logging to S3 (encrypted, 90-day lifecycle, public access blocked).
    //   • Redirect URI allowlist in handle_callback (prevents open-redirect attacks).
    //
    // NOT PROVIDED – consider adding before going to production:
    //   • Shield Advanced (L7 DDoS + SRT + cost protection – subscription required).
    //   • Bot Control TARGETED inspection level (additional WAF cost).
    //   • CloudTrail / Security Hub integration for centralised audit.
    //   • ALB access-log Athena workgroup / GuardDuty findings.
    // =============================================================================

    // =============================================================================
    // CONFIGURATION FROM CONTEXT
    // =============================================================================

    // Domain and infrastructure configuration from context
    const domainName = this.node.tryGetContext("domainName") || "";
    const hostedZoneName = this.node.tryGetContext("hostedZoneName") || "";
    const hostedZoneId = this.node.tryGetContext("hostedZoneId") || "";
    const certificateArn = this.node.tryGetContext("certificateArn") || "";

    // MCP metadata key for path-based routing (reverse DNS notation)
    // Used in _meta field to filter tools by target
    const mcpMetadataKey = this.node.tryGetContext("mcpMetadataKey") || "com.example/target";

    // =============================================================================
    // RESOURCE SERVER IDENTIFIER
    // The resource server identifier doubles as the OAuth audience claim
    // (RFC 8707 resource indicator / RFC 9728 protected-resource metadata).
    // All access tokens issued for the MCP endpoint carry this as their `aud`.
    // =============================================================================
    const resourceServerIdentifier = "agentcore-gateway";

    // =============================================================================
    // PRE-TOKEN GENERATION LAMBDA
    // =============================================================================

    // Create Lambda execution role for pre-token generation
    const preTokenLambdaRole = new iam.Role(this, "PreTokenLambdaRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSLambdaBasicExecutionRole"
        ),
      ],
    });

    // Pre-Token Generation Lambda
    const preTokenGenerationLambda = new lambda.Function(
      this,
      "PreTokenGenerationLambda",
      {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: "lambda_function.lambda_handler",
        code: lambda.Code.fromAsset(path.join(__dirname, "../lambda"), {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            command: [
              "bash",
              "-c",
              [
                "cp pre_token_generation_lambda.py /asset-output/lambda_function.py",
              ].join(" && "),
            ],
          },
        }),
        role: preTokenLambdaRole,
        timeout: cdk.Duration.seconds(60),
        memorySize: 128,
        // Bound concurrency so a token-generation burst cannot starve other
        // workloads; tune this value to your peak sign-in rate.
        reservedConcurrentExecutions: 50,
        description: "Lambda to add custom claims to Cognito tokens based on user email",
        environment: {
          // Injected into every access token as the `aud` claim so that the
          // proxy Lambda's audience validator can verify the token is scoped
          // to this resource server (RFC 8707 / MCP Authorization spec).
          RESOURCE_SERVER_ID: resourceServerIdentifier,
        },
      }
    );

    // =============================================================================
    // COGNITO USER POOL
    // =============================================================================

    // Create Cognito User Pool
    const userPool = new cognito.UserPool(this, "AgentCoreEnterprisePool", {
      userPoolName: `agentcore-enterprise-pool`,
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

    // Grant Cognito permission to invoke the pre-token generation Lambda
    preTokenGenerationLambda.addPermission("CognitoInvokePermission", {
      principal: new iam.ServicePrincipal("cognito-idp.amazonaws.com"),
      sourceArn: userPool.userPoolArn,

    });

    userPool.addTrigger(cognito.UserPoolOperation.PRE_TOKEN_GENERATION_CONFIG, preTokenGenerationLambda, cognito.LambdaVersion.V3_0);


    // Create Cognito Domain
    const cognitoDomainPrefix = `agentcore-vscode-domain-${this.account}`;
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
        identifier: resourceServerIdentifier,
        userPoolResourceServerName: "AgentCore Gateway",
        scopes: [readScope, writeScope],
      }
    );

    const mcpScopes = [
      cognito.OAuthScope.resourceServer(resourceServer, readScope),
      cognito.OAuthScope.resourceServer(resourceServer, writeScope),
    ];

    // =============================================================================
    // BEDROCK GUARDRAILS
    // =============================================================================

    const guardrails = new bedrockl1.CfnGuardrail(this, "AgentCoreGuardrail", {
      name: "AgentCore-Enterprise-Guardrail",
      description: "Guardrail for AgentCore Enterprise MCP Gateway",
      blockedInputMessaging: "Your request contains content that violates our policies and cannot be processed.",
      blockedOutputsMessaging: "The response contains content that violates our policies and cannot be displayed.",
      sensitiveInformationPolicyConfig:{
        // setting up some example PII entity types to anonymize in responses. This can be customized based on specific requirements.
        piiEntitiesConfig:[
          {
            type: 'ADDRESS',
            action: 'ANONYMIZE',
            inputEnabled: true,
            inputAction: 'ANONYMIZE'
          },
          {
            type: 'NAME',
            action: 'ANONYMIZE',
            inputEnabled: true,
            inputAction: 'ANONYMIZE'
          },
          {
            type: 'EMAIL',
            action: 'ANONYMIZE',
            inputEnabled: true,
            inputAction: 'ANONYMIZE'
          },
          {
            type: 'CREDIT_DEBIT_CARD_NUMBER',
            action: 'BLOCK',
            inputEnabled: true,
            inputAction: 'BLOCK'
          }
        ]
      }
    }
    );

    // =============================================================================
    // VPC SETUP
    // =============================================================================

    // Create a new VPC with public subnets and internet gateway
    const vpc = new ec2.Vpc(this, "McpVpc", {
        maxAzs: 2,
        natGateways: 1, // NAT Gateway for Lambda in private subnet to access internet
        subnetConfiguration: [
          {
            name: "Public",
            subnetType: ec2.SubnetType.PUBLIC,
            cidrMask: 24,
          },
          {
            name: "Private",
            subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
            cidrMask: 24,
          },
        ],
      });

      // =============================================================================
      // VPC INTERFACE ENDPOINT – bedrock-agentcore
      // Keeps all InvokeGateway traffic from the proxy Lambda on the AWS private
      // network; packets never traverse the public internet.
      //
      // Security group: allows HTTPS (443) inbound only from the VPC CIDR so that
      // only resources inside this VPC can use the endpoint.  All other traffic is
      // implicitly denied.
      //
      // NOTE: Interface endpoints incur an hourly charge per AZ plus a per-GB
      // data-processing fee.  See https://aws.amazon.com/privatelink/pricing/
      // =============================================================================
      const agentcoreEndpointSg = new ec2.SecurityGroup(
        this,
        "AgentCoreEndpointSg",
        {
          vpc,
          description:
            "Allow HTTPS from VPC to bedrock-agentcore interface endpoint",
          allowAllOutbound: false,
        }
      );
      agentcoreEndpointSg.addIngressRule(
        ec2.Peer.ipv4(vpc.vpcCidrBlock),
        ec2.Port.tcp(443),
        "HTTPS from VPC to AgentCore endpoint"
      );

      // Interface endpoint for the AgentCore data-plane (InvokeGateway).
      // Placed in the private subnets alongside the proxy Lambda so no NAT hop
      // is needed for AgentCore API calls.
      vpc.addInterfaceEndpoint("AgentCoreEndpoint", {
        service: new ec2.InterfaceVpcEndpointService(
          `com.amazonaws.${this.region}.bedrock-agentcore.gateway`,
          443
        ),
        subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
        securityGroups: [agentcoreEndpointSg],
        privateDnsEnabled: true,
      });

    // =============================================================================
    // WAF WEB ACL
    // Layers of protection applied (all free-tier managed rule groups unless noted):
    //   1. IP-level rate limit – 1 000 req / 5 min per source IP
    //   2. AWSManagedRulesCommonRuleSet (CRS) – OWASP Top 10 signatures
    //   3. AWSManagedRulesKnownBadInputsRuleSet – known attack patterns
    //   4. AWSManagedRulesAmazonIpReputationList – AWS threat-intel IP block list
    //   5. AWSManagedRulesBotControlRuleSet (common bots, COUNT mode) – set to
    //      COUNT so legitimate MCP clients are not accidentally blocked during
    //      testing; switch to BLOCK in production after validating traffic.
    //
    // NOTE: rules 2–5 are scoped-down to exclude the OAuth flow endpoints
    // (/token, /authorize, /callback, /register) whose bodies legitimately
    // contain patterns (redirect_uri, code_verifier, grant_type, etc.) that
    // signature-based rules would otherwise flag as false positives.
    //
    // DDoS protection: Shield Standard is enabled automatically on any public
    // ALB at no extra cost; it mitigates volumetric L3/L4 attacks.  For L7
    // DDoS protection and SRT access, subscribe to Shield Advanced separately.
    // =============================================================================

    // Helper: re-usable OAuth-path scope-down statement (shared by CRS, KBI,
    // IP-reputation and Bot-Control rules so we don't repeat the block 4 times).
    const oauthScopeDown: wafv2.CfnWebACL.StatementProperty = {
      notStatement: {
        statement: {
          orStatement: {
            statements: [
              {
                byteMatchStatement: {
                  searchString: "/token",
                  fieldToMatch: { uriPath: {} },
                  textTransformations: [{ priority: 0, type: "LOWERCASE" }],
                  positionalConstraint: "EXACTLY",
                },
              },
              {
                byteMatchStatement: {
                  searchString: "/authorize",
                  fieldToMatch: { uriPath: {} },
                  textTransformations: [{ priority: 0, type: "LOWERCASE" }],
                  positionalConstraint: "EXACTLY",
                },
              },
              {
                byteMatchStatement: {
                  searchString: "/callback",
                  fieldToMatch: { uriPath: {} },
                  textTransformations: [{ priority: 0, type: "LOWERCASE" }],
                  positionalConstraint: "EXACTLY",
                },
              },
              {
                byteMatchStatement: {
                  searchString: "/register",
                  fieldToMatch: { uriPath: {} },
                  textTransformations: [{ priority: 0, type: "LOWERCASE" }],
                  positionalConstraint: "EXACTLY",
                },
              },
            ],
          },
        },
      },
    };

    let webAcl: wafv2.CfnWebACL;

    webAcl = new wafv2.CfnWebACL(this, "McpAlbWebAcl", {
        name: "mcp-alb-web-acl",
        scope: "REGIONAL",
        defaultAction: { allow: {} },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName: "mcp-alb-web-acl",
          sampledRequestsEnabled: true,
        },
        rules: [
          // ── 1. IP-level rate limit ────────────────────────────────────────────
          // 1 000 requests per 5-minute window per source IP.
          {
            name: "RateLimit",
            priority: 1,
            action: { block: {} },
            visibilityConfig: {
              cloudWatchMetricsEnabled: true,
              metricName: "RateLimit",
              sampledRequestsEnabled: true,
            },
            statement: {
              rateBasedStatement: {
                limit: 1000,
                aggregateKeyType: "IP",
              },
            },
          },

          // ── 2. AWS IP Reputation list ─────────────────────────────────────────
          // Blocks IPs on AWS-maintained threat-intel lists (botnets, TOR exit
          // nodes, scanners).  Applied before expensive rule evaluation.
          {
            name: "AWSManagedRulesIPReputation",
            priority: 2,
            overrideAction: { none: {} },
            visibilityConfig: {
              cloudWatchMetricsEnabled: true,
              metricName: "AWSManagedRulesIPReputation",
              sampledRequestsEnabled: true,
            },
            statement: {
              managedRuleGroupStatement: {
                vendorName: "AWS",
                name: "AWSManagedRulesAmazonIpReputationList",
                scopeDownStatement: oauthScopeDown,
              },
            },
          },

          // ── 3. Core Rule Set (OWASP Top 10) ──────────────────────────────────
          {
            name: "AWSManagedRulesCRS",
            priority: 3,
            overrideAction: { none: {} },
            visibilityConfig: {
              cloudWatchMetricsEnabled: true,
              metricName: "AWSManagedRulesCRS",
              sampledRequestsEnabled: true,
            },
            statement: {
              managedRuleGroupStatement: {
                vendorName: "AWS",
                name: "AWSManagedRulesCommonRuleSet",
                scopeDownStatement: oauthScopeDown,
              },
            },
          },

          // ── 4. Known Bad Inputs ───────────────────────────────────────────────
          {
            name: "AWSManagedRulesKnownBadInputs",
            priority: 4,
            overrideAction: { none: {} },
            visibilityConfig: {
              cloudWatchMetricsEnabled: true,
              metricName: "AWSManagedRulesKnownBadInputs",
              sampledRequestsEnabled: true,
            },
            statement: {
              managedRuleGroupStatement: {
                vendorName: "AWS",
                name: "AWSManagedRulesKnownBadInputsRuleSet",
                scopeDownStatement: oauthScopeDown,
              },
            },
          },

          // ── 5. Bot Control (common bots – COUNT mode) ─────────────────────────
          // Runs in COUNT so automated MCP clients are not accidentally blocked
          // during testing/piloting.  Review CloudWatch metrics and switch
          // overrideAction to { none: {} } (BLOCK) once traffic is validated.
          {
            name: "AWSManagedRulesBotControl",
            priority: 5,
            overrideAction: { count: {} },
            visibilityConfig: {
              cloudWatchMetricsEnabled: true,
              metricName: "AWSManagedRulesBotControl",
              sampledRequestsEnabled: true,
            },
            statement: {
              managedRuleGroupStatement: {
                vendorName: "AWS",
                name: "AWSManagedRulesBotControlRuleSet",
                managedRuleGroupConfigs: [
                  { awsManagedRulesBotControlRuleSet: { inspectionLevel: "COMMON" } },
                ],
                scopeDownStatement: oauthScopeDown,
              },
            },
          },
        ],
      });

    // =============================================================================
    // LAMBDA FUNCTIONS
    // =============================================================================

    // ---------------------------------------------------------------------------
    // SECURITY: Dedicated least-privilege IAM role per Lambda function group.
    //
    // Role              │ Used by                        │ Permissions
    // ──────────────────┼────────────────────────────────┼────────────────────────
    // proxyLambdaRole   │ McpProxyLambda (VPC-resident)  │ VPC execution +
    //                   │                                │ bedrock-agentcore:InvokeGateway (scoped to gateway ARN after creation)
    //                   │                                │ bedrock-agentcore:CompleteResourceTokenAuth / GetResourceOauth2Token
    // interceptorRole   │ McpInterceptorLambda           │ Basic execution +
    //                   │                                │ bedrock:ApplyGuardrail (scoped to this guardrail)
    // toolLambdaRole    │ WeatherLambda, InventoryLambda,│ Basic execution only –
    //                   │ UserDetailsLambda              │ tool Lambdas receive events from AgentCore
    //                   │                                │ and need no AWS API permissions
    // ---------------------------------------------------------------------------

    // ── Proxy Lambda role ────────────────────────────────────────────────────────
    // Needs VPC access (private subnet) + AgentCore gateway invocation.
    // NOTE: secretsmanager resource is scoped to "*" here as a placeholder –
    //       replace with the exact secret ARN once you create your Secrets Manager
    //       secret for the OAuth client credentials.
    const proxyLambdaRole = new iam.Role(this, "McpProxyLambdaRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      description: "Least-privilege role for the MCP proxy Lambda (VPC-resident)",
      managedPolicies: [
        // Basic CloudWatch Logs permissions
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSLambdaBasicExecutionRole"
        ),
        // ENI create/describe/delete for VPC placement
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSLambdaVPCAccessExecutionRole"
        ),
      ],
    });

    // AgentCore identity token exchange – scoped to gateway ARN added after
    // gateway creation (see proxyLambdaRole.addToPolicy below the gateway block).
    proxyLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "AgentCoreIdentityTokenExchange",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock-agentcore:CompleteResourceTokenAuth",
          "bedrock-agentcore:GetResourceOauth2Token",
        ],
        // These actions do not support resource-level conditions in the current
        // AgentCore IAM reference; restrict once supported.
        resources: ["*"],
      })
    );

    // bedrock-agentcore:InvokeGateway is added below the gateway construct so we
    // can scope it to the specific gateway ARN.

    // ── Interceptor Lambda role ──────────────────────────────────────────────────
    // Only needs to call bedrock:ApplyGuardrail on this specific guardrail.
    const interceptorLambdaRole = new iam.Role(this, "McpInterceptorLambdaRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      description: "Least-privilege role for the MCP interceptor Lambda",
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSLambdaBasicExecutionRole"
        ),
      ],
    });

    interceptorLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "ApplyGuardrailThisGuardrailOnly",
        effect: iam.Effect.ALLOW,
        actions: ["bedrock:ApplyGuardrail"],
        // Scoped to the exact guardrail created in this stack.
        resources: [guardrails.attrGuardrailArn],
      })
    );

    // ── Tool Lambda role ─────────────────────────────────────────────────────────
    // Shared by WeatherLambda, InventoryLambda, and UserDetailsLambda.
    // These Lambdas are invoked by AgentCore and only need CloudWatch Logs access.
    // They do NOT require any Bedrock, Secrets Manager, or AgentCore permissions.
    const toolLambdaRole = new iam.Role(this, "McpToolLambdaRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      description:
        "Least-privilege role for MCP tool Lambdas (weather, inventory, user-details) - no AWS API permissions required",
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSLambdaBasicExecutionRole"
        ),
      ],
    });

    // MCP Proxy Lambda (with increased timeout for ALB)
    // Reserved concurrency: cap concurrency so a traffic burst cannot exhaust the
    // account limit and starve other workloads.  Tune per your traffic profile.
    const proxyLambda = new lambda.Function(this, "McpProxyLambda", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda_function.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../lambda"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            "bash",
            "-c",
            ["cp mcp_proxy_lambda.py /asset-output/lambda_function.py"].join(
              " && "
            ),
          ],
        },
      }),
      role: proxyLambdaRole,
      timeout: cdk.Duration.seconds(300), // 5 minutes for ALB
      memorySize: 256,
      vpc: vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      reservedConcurrentExecutions: 100,
      environment: {
        GATEWAY_URL: "", // Will be updated after gateway creation
        COGNITO_DOMAIN: `https://${cognitoDomain.domainName}.auth.${this.region}.amazoncognito.com`,
        CLIENT_ID: "", // Will be updated after VS Code client creation
        // CLIENT_SECRET: "",
        CALLBACK_LAMBDA_URL: "", // Will be updated after ALB creation
        // The resource server identifier is used for audience validation.
        // Tokens whose `aud` claim does not contain this value are rejected
        // before being forwarded to the AgentCore Gateway.
        RESOURCE_SERVER_ID: resourceServerIdentifier,
        COGNITO_USER_POOL_ID: userPool.userPoolId,
        COGNITO_REGION: this.region,
        // MCP metadata key for path-based routing
        MCP_METADATA_KEY: mcpMetadataKey,
      },
    });

    // Weather Lambda
    const weatherLambda = new lambda.Function(this, "WeatherLambda", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda_function.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../lambda/mcp-servers/weather"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            "bash",
            "-c",
            ["cp weather_lambda.py /asset-output/lambda_function.py"].join(
              " && "
            ),
          ],
        },
      }),
      role: toolLambdaRole,
      timeout: cdk.Duration.seconds(300), // 5 minutes for ALB
      memorySize: 256,
      reservedConcurrentExecutions: 50,
    });

    // Inventory Lambda
    const inventoryLambda = new lambda.Function(this, "InventoryLambda", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda_function.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../lambda/mcp-servers/inventory"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            "bash",
            "-c",
            ["cp inventory_lambda.py /asset-output/lambda_function.py"].join(
              " && "
            ),
          ],
        },
      }),
      role: toolLambdaRole,
      timeout: cdk.Duration.seconds(300), // 5 minutes for ALB
      memorySize: 256,
      reservedConcurrentExecutions: 50,
    });

    // User Details Lambda
    const userDetailsLambda = new lambda.Function(this, "UserDetailsLambda", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda_function.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../lambda/mcp-servers/user_details"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            "bash",
            "-c",
            ["cp user_details_lambda.py /asset-output/lambda_function.py"].join(
              " && "
            ),
          ],
        },
      }),
      role: toolLambdaRole,
      timeout: cdk.Duration.seconds(300), // 5 minutes for ALB
      memorySize: 256,
      reservedConcurrentExecutions: 50,
    });


    // Interceptor Lambda
    const interceptorLambda = new lambda.Function(this, "McpInterceptorLambda", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda_function.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../lambda/interceptor"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            "bash",
            "-c",
            ["cp interceptor.py /asset-output/lambda_function.py"].join(
              " && "
            ),
          ],
        },
      }),
      role: interceptorLambdaRole,
      timeout: cdk.Duration.seconds(300), // 5 minutes for ALB
      memorySize: 256,
      reservedConcurrentExecutions: 50,
      environment: {
        "GUARDRAIL_ID": guardrails.attrGuardrailId,
        "GUARDRAIL_VERSION": guardrails.attrVersion,
        "MCP_METADATA_KEY": mcpMetadataKey,
      },
    });

    // =============================================================================
    // APPLICATION LOAD BALANCER
    // =============================================================================

    let endpointUrl: string;

    // Security group: accept HTTPS (443) and HTTP (80) only.
    // All other inbound traffic is implicitly denied.
    const albSecurityGroup = new ec2.SecurityGroup(this, "AlbSecurityGroup", {
      vpc: vpc,
        description: "ALB security group - HTTPS/HTTP ingress only",
        allowAllOutbound: true,
      });
      albSecurityGroup.addIngressRule(
        ec2.Peer.anyIpv4(),
        ec2.Port.tcp(443),
        "Allow HTTPS from the internet"
      );
      albSecurityGroup.addIngressRule(
        ec2.Peer.anyIpv4(),
        ec2.Port.tcp(80),
        "Allow HTTP from the internet (redirected to HTTPS)"
      );
      albSecurityGroup.addIngressRule(
        ec2.Peer.anyIpv6(),
        ec2.Port.tcp(443),
        "Allow HTTPS from the internet (IPv6)"
      );
      albSecurityGroup.addIngressRule(
        ec2.Peer.anyIpv6(),
        ec2.Port.tcp(80),
        "Allow HTTP from the internet (IPv6, redirected to HTTPS)"
      );

      // =============================================================================
      // ALB ACCESS LOG BUCKET
      // S3 bucket for ALB access logs with encryption, lifecycle, and public
      // access blocked.  The CDK logAccessLogs() helper automatically grants
      // the correct regional ELB service account write access via bucket policy.
      // Requires a concrete region in the stack env (set in bin/enterprise-mcp-infra.ts).
      // =============================================================================
      const albLogBucket = new s3.Bucket(this, "AlbAccessLogBucket", {
        bucketName: `mcp-alb-access-logs-${this.account}-${this.region}`,
        encryption: s3.BucketEncryption.S3_MANAGED,
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        enforceSSL: true,
        versioned: false,
        lifecycleRules: [
          {
            id: "ExpireAfter90Days",
            expiration: cdk.Duration.days(90),
          },
        ],
        removalPolicy: cdk.RemovalPolicy.DESTROY,
        autoDeleteObjects: true, // NOTE: set to false in production to prevent accidental log loss
      });

      // Create Application Load Balancer.
      // dropInvalidHeaderFields: rejects requests whose headers contain
      // characters outside the RFC 7230 allowed set, blocking several
      // request-smuggling / header-injection attack vectors.
      const alb = new elbv2.ApplicationLoadBalancer(this, "McpOAuthProxyALB", {
        vpc: vpc,
        internetFacing: true,
        loadBalancerName: "mcp-oauth-proxy-alb",
        securityGroup: albSecurityGroup,
        dropInvalidHeaderFields: true,
      });

      // Enable ALB access logging. logAccessLogs() sets the correct bucket
      // policy for the regional ELB account automatically.
      alb.logAccessLogs(albLogBucket, "alb");

      // Associate the WAF WebACL with the ALB
      new wafv2.CfnWebACLAssociation(this, "AlbWebAclAssociation", {
        resourceArn: alb.loadBalancerArn,
        webAclArn: webAcl.attrArn,
      });

      // Import the certificate
      const certificate = certificatemanager.Certificate.fromCertificateArn(
        this,
        "AlbCertificate",
        certificateArn
      );

      // Create HTTPS Listener
      const mainListener = alb.addListener("HttpsListener", {
        port: 443,
        protocol: elbv2.ApplicationProtocol.HTTPS,
        certificates: [certificate],
        defaultAction: elbv2.ListenerAction.fixedResponse(404, {
          contentType: "text/plain",
          messageBody: "Not Found",
        }),
      });

      // Add HTTP listener that redirects to HTTPS
      alb.addListener("HttpListener", {
        port: 80,
        protocol: elbv2.ApplicationProtocol.HTTP,
        defaultAction: elbv2.ListenerAction.redirect({
          protocol: "HTTPS",
          port: "443",
          permanent: true,
        }),
      });

      // Import the hosted zone
      const hostedZone = route53.HostedZone.fromHostedZoneAttributes(
        this,
        "HostedZone",
        {
          hostedZoneId: hostedZoneId,
          zoneName: hostedZoneName,
        }
      );

      // Create DNS record pointing to the ALB
      new route53.ARecord(this, "AlbAliasRecord", {
        zone: hostedZone,
        recordName: domainName,
        target: route53.RecordTarget.fromAlias(
          new route53targets.LoadBalancerTarget(alb)
        ),
      });

      // Create Lambda Target Group
      const proxyTargetGroup = new elbv2.ApplicationTargetGroup(
        this,
        "ProxyTargetGroup",
        {
          vpc,
          targetType: elbv2.TargetType.LAMBDA,
          targets: [new targets.LambdaTarget(proxyLambda)],
          healthCheck: {
            enabled: true,
            path: "/ping",
            interval: cdk.Duration.seconds(300),
          },
        }
      );

      // Grant ALB permission to invoke Lambda
      proxyLambda.grantInvoke(
        new iam.ServicePrincipal("elasticloadbalancing.amazonaws.com")
      );

      // Host-header condition: every forwarding rule requires the Host header to
      // match the custom domain.  Requests arriving via the raw ALB DNS name
      // (*.elb.amazonaws.com) fall through to the listener's default 404 action
      // and are never forwarded to the Lambda.
      // This prevents virtual-hosting exploitation and removes the raw DNS name
      // as a valid entry point that bypasses your WAF / custom-domain TLS policy.
      const hostHeaderCondition = elbv2.ListenerCondition.hostHeaders([
        `${domainName}.${hostedZoneName}`,
      ]);

      // Proxy Lambda routes - specific paths
      mainListener.addTargetGroups("ProxyWellKnownAuthRule", {
        priority: 40,
        conditions: [
          hostHeaderCondition,
          elbv2.ListenerCondition.pathPatterns([
            "/.well-known/oauth-authorization-server",
          ]),
        ],
        targetGroups: [proxyTargetGroup],
      });

      mainListener.addTargetGroups("ProxyWellKnownResourceRule", {
        priority: 50,
        conditions: [
          hostHeaderCondition,
          elbv2.ListenerCondition.pathPatterns([
            "/.well-known/oauth-protected-resource",
          ]),
        ],
        targetGroups: [proxyTargetGroup],
      });

      mainListener.addTargetGroups("ProxyAuthorizeRule", {
        priority: 60,
        conditions: [
          hostHeaderCondition,
          elbv2.ListenerCondition.pathPatterns(["/authorize"]),
        ],
        targetGroups: [proxyTargetGroup],
      });

      mainListener.addTargetGroups("ProxyCallbackRule", {
        priority: 70,
        conditions: [
          hostHeaderCondition,
          elbv2.ListenerCondition.pathPatterns(["/callback"]),
        ],
        targetGroups: [proxyTargetGroup],
      });

      mainListener.addTargetGroups("ProxyTokenRule", {
        priority: 80,
        conditions: [
          hostHeaderCondition,
          elbv2.ListenerCondition.pathPatterns(["/token"]),
        ],
        targetGroups: [proxyTargetGroup],
      });

      mainListener.addTargetGroups("ProxyRegisterRule", {
        priority: 90,
        conditions: [
          hostHeaderCondition,
          elbv2.ListenerCondition.pathPatterns(["/register"]),
        ],
        targetGroups: [proxyTargetGroup],
      });

      // MCP routes - wildcard pattern for dynamic target filtering
      // Matches: /mcp, /gitlab/mcp, /weather/mcp, /inventory/mcp, /*/mcp
      // No need to update ALB when adding new tool groups!
      mainListener.addTargetGroups("ProxyMcpWildcardRule", {
        priority: 95,
        conditions: [
          hostHeaderCondition,
          elbv2.ListenerCondition.pathPatterns(["/mcp", "/*/mcp"]),
        ],
        targetGroups: [proxyTargetGroup],
      });

      // Default catch-all rule for Proxy Lambda (still host-header gated)
      mainListener.addTargetGroups("ProxyDefaultRule", {
        priority: 100,
        conditions: [
          hostHeaderCondition,
          elbv2.ListenerCondition.pathPatterns(["/*"]),
        ],
        targetGroups: [proxyTargetGroup],
      });

      // Use custom domain as endpoint
      endpointUrl = `https://${domainName}.${hostedZoneName}`;

      // Outputs for ALB
      new cdk.CfnOutput(this, "AlbEndpoint", {
        value: endpointUrl,
        description: "ALB Endpoint (HTTPS with Custom Domain)",
      });

      new cdk.CfnOutput(this, "CustomDomain", {
        value: domainName,
        description: "Custom Domain Name",
      });

      new cdk.CfnOutput(this, "AlbDnsName", {
        value: alb.loadBalancerDnsName,
        description: "ALB DNS Name",
      });

    // =============================================================================
    // VS CODE COGNITO CLIENT (with callback URLs)
    // =============================================================================

    const callbackUrls = [
      "http://127.0.0.1:33418",
      "http://127.0.0.1:33418/",
      "http://localhost:33418",
      "http://localhost:33418/",
      "http://localhost:54038",
      "http://localhost:54038/",
      `${endpointUrl}/callback`,
      `${endpointUrl}/callback/`,
      "https://vscode.dev/redirect",
      "https://insiders.vscode.dev/redirect",
    ];

    const vscodeClient = userPool.addClient("VSCodeClient", {
      userPoolClientName: `agentcore-vscode`,
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
      },
      supportedIdentityProviders: [
        cognito.UserPoolClientIdentityProvider.COGNITO,
      ],
    });

    // Update Lambda environment variables with VS Code client ID and endpoint
    proxyLambda.addEnvironment("CLIENT_ID", vscodeClient.userPoolClientId);
    proxyLambda.addEnvironment("CALLBACK_LAMBDA_URL", endpointUrl);
    // Pass the Cognito-registered callback URLs so the proxy Lambda can
    // validate redirect_uri in handle_callback (open-redirect prevention).
    proxyLambda.addEnvironment("ALLOWED_REDIRECT_URIS", JSON.stringify(callbackUrls));

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
                "bedrock-agentcore:GetPolicyEngine",
                "bedrock-agentcore:AuthorizeAction",
                "bedrock-agentcore:PartiallyAuthorizeActions",
                "bedrock-agentcore:CheckAuthorizePermissions"
              ],
              resources: ["*"],
              effect: iam.Effect.ALLOW,
            }),
          ],
        }),
      },
    });

    const gateway = new agentcore.Gateway(this, "AgentCoreMcpGateway", {
      gatewayName: `agentcore-mcp-gateway-${this.account}`,
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
        allowedAudiences: [vscodeClient.userPoolClientId],
        allowedScopes: mcpScopes.map((s) => s.scopeName),
      }),
      interceptorConfigurations: [
        agentcore.LambdaInterceptor.forRequest(interceptorLambda, { passRequestHeaders: true }),
        agentcore.LambdaInterceptor.forResponse(interceptorLambda, { passRequestHeaders: true })
      ],
    });

    const toolSchema = agentcore.ToolSchema.fromInline([
			{
				name: 'get_weather',
				description: "Get weather for a location",
				inputSchema: {
					type: agentcore.SchemaDefinitionType.OBJECT,
					properties: {
						timezone: {
							type: agentcore.SchemaDefinitionType.STRING,
							description: "the location e.g. seattle, wa"
						}
					}
				}
			}
		]);

    gateway.addLambdaTarget("WeatherLambdaTarget", {
      lambdaFunction: weatherLambda,
      gatewayTargetName: "weather-tool",
      toolSchema: toolSchema,
      credentialProviderConfigurations:[agentcore.GatewayCredentialProvider.fromIamRole()]
    });

    const inventoryToolSchema = agentcore.ToolSchema.fromInline([
			{
				name: 'get_inventory',
				description: "Get inventory for a product",
				inputSchema: {
					type: agentcore.SchemaDefinitionType.OBJECT,
					properties: {
						productId: {
							type: agentcore.SchemaDefinitionType.STRING,
							description: "the product ID to check inventory for"
						}
					}
				}
			}
		]);

    const userDetailsToolSchema = agentcore.ToolSchema.fromInline([
      {
        name: 'get_user_email',
        description: "Get user email for a user",
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            userId: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: "the user ID to get email for"
            }
          }
        }
      },
      {
        name: 'get_user_cc_number',
        description: "Get user credit card number for a user",
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            userId: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: "the user ID to get credit card number for"
            }
          }
        }
      }
    ]);

    gateway.addLambdaTarget("InventoryLambdaTarget", {
      lambdaFunction: inventoryLambda,
      gatewayTargetName: "inventory-tool",
      toolSchema: inventoryToolSchema,
      credentialProviderConfigurations:[agentcore.GatewayCredentialProvider.fromIamRole()]
    });

    gateway.addLambdaTarget("UserDetailsLambdaTarget", {
      lambdaFunction: userDetailsLambda,
      gatewayTargetName: "user-details-tool",
      toolSchema: userDetailsToolSchema,
      credentialProviderConfigurations:[agentcore.GatewayCredentialProvider.fromIamRole()]
    });

    proxyLambda.addEnvironment("GATEWAY_URL", gateway.gatewayUrl ?? "");

    // Now that the gateway ARN is known, scope InvokeGateway to this gateway only.
    // This must come AFTER the gateway construct so CDK can resolve the ARN token.
    proxyLambdaRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "InvokeThisGatewayOnly",
        effect: iam.Effect.ALLOW,
        actions: ["bedrock-agentcore:InvokeGateway"],
        // Scoped to the specific gateway ARN – not wildcard "*".
        resources: [gateway.gatewayArn],
      })
    );

    // =============================================================================
    // GATEWAY RESOURCE-BASED POLICY (VPC restriction)
    // =============================================================================

    // Create a custom resource to attach VPC-based policy to the gateway
    const policyCustomResourceRole = new iam.Role(
        this,
        "GatewayPolicyCustomResourceRole",
        {
          assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
          managedPolicies: [
            iam.ManagedPolicy.fromAwsManagedPolicyName(
              "service-role/AWSLambdaBasicExecutionRole"
            ),
          ],
        }
      );

      // Add permissions to manage gateway resource policy
      policyCustomResourceRole.addToPolicy(
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: [
            "bedrock-agentcore:PutResourcePolicy",
            "bedrock-agentcore:GetResourcePolicy",
            "bedrock-agentcore:DeleteResourcePolicy",
          ],
          resources: [gateway.gatewayArn],
        })
      );

      // Create Lambda layer with boto3 1.42.69
      const boto3Layer = new lambda.LayerVersion(this, "Boto3Layer", {
        code: lambda.Code.fromAsset(path.join(__dirname, "../lambda"), {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            command: [
              "bash",
              "-c",
              [
                "pip install boto3==1.42.69 -t /asset-output/python",
              ].join(" && "),
            ],
          },
        }),
        compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
        description: "boto3 1.42.69 for AgentCore Gateway policy management",
      });

      // Custom resource Lambda to manage gateway resource policy
      const gatewayPolicyCustomResource = new lambda.Function(
        this,
        "GatewayPolicyCustomResource",
        {
          runtime: lambda.Runtime.PYTHON_3_12,
          handler: "index.handler",
          layers: [boto3Layer],
          code: lambda.Code.fromInline(`
import json
import boto3
import cfnresponse

bedrock_agentcore = boto3.client('bedrock-agentcore-control')

def handler(event, context):
    try:
        request_type = event['RequestType']
        gateway_id = event['ResourceProperties']['GatewayId']
        vpc_id = event['ResourceProperties']['VpcId']
        gateway_arn = event['ResourceProperties']['GatewayArn']

        if request_type in ['Create', 'Update']:
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "AllowInvokeFromVPC",
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": "bedrock-agentcore:InvokeGateway",
                        "Resource": gateway_arn,
                        "Condition": {
                            "StringEquals": {
                                "aws:SourceVpc": vpc_id
                            }
                        }
                    }
                ]
            }

            bedrock_agentcore.put_resource_policy(
                resourceArn=gateway_arn,
                policy=json.dumps(policy)
            )

            cfnresponse.send(event, context, cfnresponse.SUCCESS,
                           {'PolicyApplied': 'true'})

        elif request_type == 'Delete':
            try:
                bedrock_agentcore.delete_resource_policy(
                    resourceArn=gateway_arn
                )
            except bedrock_agentcore.exceptions.ResourceNotFoundException:
                pass  # Policy already deleted

            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})

    except Exception as e:
        print(f"Error: {str(e)}")
        cfnresponse.send(event, context, cfnresponse.FAILED,
                       {'Error': str(e)})
`),
          role: policyCustomResourceRole,
          timeout: cdk.Duration.minutes(2),
        }
      );

      // Create custom resource
      const gatewayPolicy = new cdk.CustomResource(
        this,
        "GatewayVpcPolicy",
        {
          serviceToken: gatewayPolicyCustomResource.functionArn,
          properties: {
            GatewayId: gateway.gatewayId,
            VpcId: vpc.vpcId,
            GatewayArn: gateway.gatewayArn,
          },
        }
      );

      // Ensure policy is applied after gateway is created
      gatewayPolicy.node.addDependency(gateway);

    // Create policy engine
    const agentCorePolicyEngine = new AgentCorePolicyEngine(this, "AgentCorePolicyEngine", {
      policyEngineName: `enterprise_mcp_policy_engine`,
      description: "Policy engine for AgentCore Enterprise MCP Gateway",
      region: this.region,
      gatewayRole: gatewayRole,
    });

    // Add policies to the engine FIRST
    const policyEngineStatementInventoryTool = `permit (principal is AgentCore::OAuthUser, action in [AgentCore::Action::"inventory-tool", AgentCore::Action::"weather-tool"],resource == AgentCore::Gateway::"${gateway.gatewayArn}") when {principal.hasTag("user_tag") && principal.getTag("user_tag") == "admin_user"};`;
    const policyEngineStatementWeatherTool = `permit (principal is AgentCore::OAuthUser,action in [AgentCore::Action::"weather-tool"],resource == AgentCore::Gateway::"${gateway.gatewayArn}") when {principal.hasTag("user_tag") && principal.getTag("user_tag") == "regular_user"};`;
    const policyEngineStatementUserDetailsTool = `permit (principal is AgentCore::OAuthUser,action in [AgentCore::Action::"user-details-tool"],resource == AgentCore::Gateway::"${gateway.gatewayArn}") when {principal.hasTag("user_tag")};`;

    // Add admin user policy (inventory and weather tools)
    const adminUserPolicy = agentCorePolicyEngine.addPolicy(
      "admin_user_policy",
      "Policy for admin users to access inventory and weather tools",
      policyEngineStatementInventoryTool
    );

    // Add regular user policy (weather tool only)
    const regularUserPolicy = agentCorePolicyEngine.addPolicy(
      "regular_user_policy",
      "Policy for regular users to access weather tool only",
      policyEngineStatementWeatherTool
    );

    // Add user details tool policy (only users with user_tag can access)
    const userDetailsToolPolicy = agentCorePolicyEngine.addPolicy(
      "user_details_policy",
      "Policy for users to access user details tool only if they have user_tag defined",
      policyEngineStatementUserDetailsTool
    );

    // Associate with gateway AFTER all policies are added
    agentCorePolicyEngine.associateWithGateway(gateway.gatewayId, 'ENFORCE');
    agentCorePolicyEngine.node.addDependency(interceptorLambda); // Ensure interceptor Lambda is created before policy engine association

    // Ensure the gateway VPC resource policy is applied after all Cedar policies
    gatewayPolicy.node.addDependency(agentCorePolicyEngine);

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

    new cdk.CfnOutput(this, "VSCodeClientId", {
      value: vscodeClient.userPoolClientId,
      description: "VS Code Client ID",
    });

    new cdk.CfnOutput(this, "EndpointUrl", {
      value: endpointUrl,
      description: "Service Endpoint URL",
    });

    new cdk.CfnOutput(this, "ProxyLambdaName", {
      value: proxyLambda.functionName,
      description: "MCP Proxy Lambda Function Name",
    });

    new cdk.CfnOutput(this, "VSCodeMcpConfig", {
      value: JSON.stringify(
        {
          servers: {
            [`enterprise-mcp-server`]: {
              type: "http",
              url: endpointUrl + "/mcp",
            },
          },
        },
        null,
        2
      ),
      description: "VS Code MCP Configuration (add to .vscode/mcp.json)",
    });

    new cdk.CfnOutput(this, "Gateway", {
      value: gateway.gatewayId,
      description: "Gateway ID",
    });

    new cdk.CfnOutput(this, "PreTokenGenerationLambdaName", {
      value: preTokenGenerationLambda.functionName,
      description: "Pre-Token Generation Lambda Function Name",
    });

    new cdk.CfnOutput(this, "PreTokenGenerationLambdaArn", {
      value: preTokenGenerationLambda.functionArn,
      description: "Pre-Token Generation Lambda Function ARN",
    });
  }
}
