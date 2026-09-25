# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""Config-driven CloudFront stack for the ``agwcd`` CLI.

Reads an ``agwcd.json`` (path from the ``agwcd_config`` context variable) and
synthesizes one CloudFront distribution that reverse-proxies every configured
route/endpoint on the custom domain and rewrites OAuth / A2A discovery so it
resolves against the custom domain instead of the raw gateway hostname.

See the module docstrings under ``custom_domains/targets`` and ``lambda/``
for the architecture.
"""

import json
from pathlib import Path
from urllib.parse import urlparse

import aws_cdk as cdk
from agwcd.config import Config
from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_certificatemanager as acm,
)
from aws_cdk import (
    aws_cloudfront as cloudfront,
)
from aws_cdk import (
    aws_cloudfront_origins as origins,
)
from aws_cdk import (
    aws_cloudwatch as cw,
)
from aws_cdk import (
    aws_cloudwatch_actions as cw_actions,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_kms as kms,
)
from aws_cdk import (
    aws_lambda as _lambda,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_route53 as route53,
)
from aws_cdk import (
    aws_route53_targets as targets,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from aws_cdk import (
    aws_sns as sns,
)
from aws_cdk import (
    aws_wafv2 as wafv2,
)
from cdk_nag import NagPackSuppression, NagSuppressions
from constructs import Construct

from custom_domains import synth

# Custom origin header — the gateway should reject requests without it.
ORIGIN_SECRET_HEADER = "X-AgentCore-Origin-Verify"
DISCOVERY_LAMBDA_DIR = "lambda/discovery"
LAMBDA_RUNTIME = _lambda.Runtime.PYTHON_3_14


class CustomDomainsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        config_path = self.node.try_get_context("agwcd_config") or "agwcd.json"
        config = Config.load(config_path)
        plan = synth.build(config)

        domain_name = config.domain_name

        # ---- per-gateway origin verification (opt-in) ----------------------
        # For each gateway that opted into origin verification, create one
        # secret + one sample REQUEST interceptor Lambda. The secret value is
        # injected as a custom origin header on that gateway's CloudFront origin
        # (below) and passed to the discovery Lambda so its downstream fetches
        # to that gateway carry it too. Gateways without verification get no
        # header and no interceptor.
        gateway_secrets: dict[str, secretsmanager.Secret] = {}
        interceptor_fns: dict[str, _lambda.Function] = {}
        for i, gw in enumerate(plan.origin_verify_gateways):
            secret = secretsmanager.Secret(
                self,
                f"OriginVerifySecret{i}",
                description=f"CloudFront origin verification header value for {gw}",
                generate_secret_string=secretsmanager.SecretStringGenerator(
                    exclude_punctuation=True,
                    password_length=32,
                ),
            )
            NagSuppressions.add_resource_suppressions(
                secret,
                [
                    NagPackSuppression(
                        id="AwsSolutions-SMG4",
                        reason=(
                            "Static CloudFront origin-verification header, not a "
                            "rotatable credential. Rotating it would require "
                            "coordinated atomic updates across CloudFront, Secrets "
                            "Manager, and the Lambda environment variable."
                        ),
                    ),
                ],
            )
            gateway_secrets[gw] = secret
            interceptor = _lambda.Function(
                self,
                f"OriginVerifyInterceptor{i}",
                runtime=LAMBDA_RUNTIME,
                handler="index.lambda_handler",
                code=_lambda.Code.from_asset("lambda/origin_verify"),
                # Pass the secret ARN, not the value: the plaintext must never
                # land in an environment variable. The interceptor fetches it
                # from Secrets Manager at runtime (cached per warm container).
                environment={
                    "ORIGIN_VERIFY_HEADER": ORIGIN_SECRET_HEADER,
                    "ORIGIN_VERIFY_SECRET_ARN": secret.secret_arn,
                },
                log_group=logs.LogGroup(
                    self,
                    f"OriginVerifyInterceptor{i}Logs",
                    retention=logs.RetentionDays.THREE_MONTHS,
                    removal_policy=RemovalPolicy.DESTROY,
                ),
            )
            secret.grant_read(interceptor)
            interceptor_fns[gw] = interceptor

        def origin_headers_for(gw: str):
            if gw in gateway_secrets:
                # The literal value is required here: CloudFront custom origin
                # headers are a static string, not a Secrets Manager reference.
                # This is the documented origin-verification pattern; unlike the
                # Lambda env vars, it is not exposed to any Lambda read role.
                return {
                    ORIGIN_SECRET_HEADER: gateway_secrets[
                        gw
                    ].secret_value.unsafe_unwrap()
                }
            return None

        # ---- DNS + TLS -----------------------------------------------------
        # Real deploys look the zone up (needs creds). The `agwcd_stub_zone`
        # context flag substitutes a synth-only stub so cdk-nag can run in CI
        # with no AWS credentials; it must never be set for a real deploy.
        if self.node.try_get_context("agwcd_stub_zone"):
            hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
                self,
                "HostedZone",
                hosted_zone_id="Z00000000000000000000",
                zone_name=domain_name,
            )
        else:
            hosted_zone = route53.PublicHostedZone.from_lookup(
                self, "HostedZone", domain_name=domain_name
            )
        certificate = acm.Certificate(
            self,
            "SSLCertificate",
            domain_name=domain_name,
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )
        certificate.apply_removal_policy(RemovalPolicy.RETAIN)

        # ---- WAF (CloudFront scope, us-east-1) -----------------------------
        web_acl = self._build_web_acl()

        # WAF logging → CloudWatch. The log-group name MUST use the mandatory
        # `aws-waf-logs-` prefix or PutLoggingConfiguration fails. Redact the
        # `authorization` header so bearer tokens are never written to logs.
        waf_log_group = logs.LogGroup(
            self,
            "WafLogs",
            log_group_name="aws-waf-logs-agwcd",
            retention=logs.RetentionDays.THREE_MONTHS,
            removal_policy=RemovalPolicy.DESTROY,
        )
        wafv2.CfnLoggingConfiguration(
            self,
            "WafLogging",
            resource_arn=web_acl.attr_arn,
            # WAF rejects a log-group ARN with a trailing ``:*``; strip it.
            log_destination_configs=[
                cdk.Fn.select(0, cdk.Fn.split(":*", waf_log_group.log_group_arn))
            ],
            redacted_fields=[
                wafv2.CfnLoggingConfiguration.FieldToMatchProperty(
                    single_header={"Name": "authorization"}
                )
            ],
        )

        # ---- access logs + alarm topic -------------------------------------
        log_bucket = s3.Bucket(
            self,
            "CloudFrontLogsBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.OBJECT_WRITER,
            lifecycle_rules=[s3.LifecycleRule(expiration=cdk.Duration.days(90))],
        )
        NagSuppressions.add_resource_suppressions(
            log_bucket,
            [
                NagPackSuppression(
                    id="AwsSolutions-S1",
                    reason=(
                        "This IS the access-log destination bucket. Enabling access "
                        "logging on a log bucket creates circular logging."
                    ),
                )
            ],
        )
        # Customer-managed KMS key (rotation on) so the alarm topic is encrypted
        # at rest (cdk-nag AwsSolutions-SNS2). CloudWatch Alarms must be able to
        # encrypt the messages it publishes.
        alarm_key = kms.Key(
            self,
            "AlarmKey",
            enable_key_rotation=True,
            description="Encrypts the agwcd CloudWatch-alarm SNS topic at rest",
        )
        alarm_key.grant_encrypt_decrypt(
            iam.ServicePrincipal("cloudwatch.amazonaws.com")
        )
        alarm_topic = sns.Topic(
            self,
            "AlarmTopic",
            display_name="AgentCore Gateway Alarms",
            master_key=alarm_key,
        )
        alarm_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="EnforceSSL",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["sns:Publish"],
                resources=[alarm_topic.topic_arn],
                conditions={"Bool": {"aws:SecureTransport": "false"}},
            )
        )

        # ---- discovery Lambda (regional, CloudFront origin) ----------------
        # Bake the routing table next to the handler so it isn't bound by the
        # 4 KB Lambda env-var limit.
        Path(DISCOVERY_LAMBDA_DIR, "routes.json").write_text(
            json.dumps(plan.routes_json, indent=2) + "\n"
        )
        # gateway base URL -> secret ARN, for verification-enabled gateways.
        # ARNs are not sensitive; the Lambda fetches each value at runtime.
        origin_verify_map = {
            gw.rstrip("/"): s.secret_arn for gw, s in gateway_secrets.items()
        }
        discovery_fn = _lambda.Function(
            self,
            "DiscoveryFunction",
            runtime=LAMBDA_RUNTIME,
            handler="index.handler",
            code=_lambda.Code.from_asset(DISCOVERY_LAMBDA_DIR),
            # A downstream doc can be slow to produce — an A2A agent card is
            # rendered by invoking the agent runtime and can take ~5-7s. Keep the
            # total under CloudFront's 30s origin-response timeout, and set the
            # per-fetch timeout (below) above the downstream latency.
            timeout=cdk.Duration.seconds(25),
            environment={
                "ORIGIN_VERIFY_HEADER": ORIGIN_SECRET_HEADER,
                "ORIGIN_VERIFY_MAP": json.dumps(origin_verify_map),
                "FETCH_TIMEOUT_SECONDS": "20",
            },
            log_group=logs.LogGroup(
                self,
                "DiscoveryFunctionLogs",
                retention=logs.RetentionDays.THREE_MONTHS,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )
        for s in gateway_secrets.values():
            s.grant_read(discovery_fn)
        discovery_fn_url = discovery_fn.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.AWS_IAM
        )
        discovery_origin = origins.FunctionUrlOrigin.with_origin_access_control(
            discovery_fn_url
        )

        # ---- edge functions ------------------------------------------------
        route_fn = cloudfront.Function(
            self,
            "RouteFunction",
            code=cloudfront.FunctionCode.from_inline(plan.function_code),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
            comment="agwcd viewer-request routing: strip route prefix, inject "
            "resource-metadata, deny unmatched",
        )
        # Viewer-request function on discovery behaviors: copy the caller's
        # bearer token into a side header (the OAC-signed origin request occupies
        # `Authorization`) so the discovery Lambda can fetch auth-protected docs.
        discovery_auth_fn = cloudfront.Function(
            self,
            "DiscoveryAuthFunction",
            code=cloudfront.FunctionCode.from_inline(plan.discovery_auth_function_code),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
            comment="agwcd discovery: forward caller Authorization as a side header",
        )
        www_auth_fn = None
        if any(b.needs_www_auth for b in plan.live_behaviors):
            # NOTE: Lambda@Edge logs are written to CloudWatch in each edge
            # region under /aws/lambda/us-east-1.<fn> at request time; the log
            # groups are created lazily per region and cannot be given an
            # explicit region-local group with a retention here at define time.
            # Retention for these must be set per-region out of band (documented
            # limitation — not faked).
            www_auth_fn = cloudfront.experimental.EdgeFunction(
                self,
                "WwwAuthRewrite",
                runtime=LAMBDA_RUNTIME,
                handler="index.handler",
                code=_lambda.Code.from_asset("lambda/www_auth"),
            )

        # ---- gateway origins (one per distinct gateway) --------------------
        gateway_origins = {
            gw: origins.HttpOrigin(
                urlparse(gw).netloc,
                protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
                custom_headers=origin_headers_for(gw),
            )
            for gw in plan.gateway_urls
        }

        route_fn_assoc = [
            cloudfront.FunctionAssociation(
                function=route_fn,
                event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
            )
        ]

        def live_behavior(gateway_url: str, needs_www_auth: bool):
            edge_lambdas = None
            if needs_www_auth and www_auth_fn is not None:
                edge_lambdas = [
                    cloudfront.EdgeLambda(
                        function_version=www_auth_fn.current_version,
                        event_type=cloudfront.LambdaEdgeEventType.ORIGIN_RESPONSE,
                    )
                ]
            return cloudfront.BehaviorOptions(
                origin=gateway_origins[gateway_url],
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                # Forwards all viewer headers (incl. the function-injected
                # x-agwcd-resource-metadata) and sets Host to the gateway origin.
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                function_associations=route_fn_assoc,
                edge_lambdas=edge_lambdas,
            )

        # The discovery Lambda needs the caller's bearer token (moved to a side
        # header by ``discovery_auth_fn``) to fetch auth-protected downstream
        # docs like the A2A agent card. With CACHING_DISABLED and no origin
        # request policy, CloudFront forwards no custom headers to the origin, so
        # the side header would be dropped and the fetch would 401. Forward just
        # that header (not Host — the OAC-signed Function URL origin needs its
        # own host).
        discovery_origin_request_policy = cloudfront.OriginRequestPolicy(
            self,
            "DiscoveryOriginRequestPolicy",
            header_behavior=cloudfront.OriginRequestHeaderBehavior.allow_list(
                synth.FORWARDED_AUTH_HEADER
            ),
            query_string_behavior=cloudfront.OriginRequestQueryStringBehavior.none(),
            cookie_behavior=cloudfront.OriginRequestCookieBehavior.none(),
        )
        discovery_behavior = cloudfront.BehaviorOptions(
            origin=discovery_origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            origin_request_policy=discovery_origin_request_policy,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            function_associations=[
                cloudfront.FunctionAssociation(
                    function=discovery_auth_fn,
                    event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                )
            ],
        )

        # CloudFront evaluates cache behaviors in list order and uses the FIRST
        # whose path pattern matches (it does NOT prefer the most specific one).
        # An A2A agent card lives *under* its agent's live path
        # (``/<agent>/.well-known/agent-card.json`` vs the live ``/<agent>/*``),
        # so the discovery behaviors must be registered first — otherwise the
        # live wildcard shadows the card and it is served straight from the
        # gateway (raw ``url``, no rewrite). CDK preserves this insertion order.
        additional_behaviors = {}
        for d in plan.discovery_behaviors:
            additional_behaviors[d.path_pattern] = discovery_behavior
        for b in plan.live_behaviors:
            additional_behaviors[b.path_pattern] = live_behavior(
                b.gateway_url, b.needs_www_auth
            )

        # Default behavior denies (RouteFunction returns 403 for unmatched URIs);
        # origin is arbitrary since the function short-circuits.
        default_behavior = cloudfront.BehaviorOptions(
            origin=discovery_origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            function_associations=route_fn_assoc,
        )

        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_behavior=default_behavior,
            additional_behaviors=additional_behaviors,
            domain_names=[domain_name],
            certificate=certificate,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            web_acl_id=web_acl.attr_arn,
            enable_logging=True,
            log_bucket=log_bucket,
            log_file_prefix="cloudfront/",
            geo_restriction=cloudfront.GeoRestriction.allowlist(*config.geo_allowlist),
        )

        # OAC → Lambda function URL requires the CloudFront service principal to
        # hold BOTH lambda:InvokeFunctionUrl (added by with_origin_access_control)
        # and lambda:InvokeFunction, each scoped to this distribution's ARN.
        # Without InvokeFunction, CloudFront's correctly-signed request is
        # rejected with AccessDeniedException. See the CloudFront OAC/Lambda docs:
        # https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-lambda.html
        discovery_fn.add_permission(
            "AllowCloudFrontInvokeFunction",
            principal=iam.ServicePrincipal("cloudfront.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=(
                f"arn:{self.partition}:cloudfront::{self.account}:"
                f"distribution/{distribution.distribution_id}"
            ),
        )

        # ---- alarms --------------------------------------------------------
        for name, metric, threshold, desc in [
            (
                "5xxErrorAlarm",
                distribution.metric5xx_error_rate(),
                5,
                "CloudFront 5xx error rate > 5%",
            ),
            (
                "4xxErrorAlarm",
                distribution.metric4xx_error_rate(),
                20,
                "CloudFront 4xx error rate > 20%",
            ),
        ]:
            alarm = cw.Alarm(
                self,
                name,
                metric=metric,
                threshold=threshold,
                evaluation_periods=3,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                alarm_description=desc,
            )
            alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        managed_role_fns = [discovery_fn, *interceptor_fns.values()]
        if www_auth_fn is not None:
            managed_role_fns.append(www_auth_fn)
        for fn in managed_role_fns:
            NagSuppressions.add_resource_suppressions(
                fn,
                [
                    NagPackSuppression(
                        id="AwsSolutions-IAM4",
                        reason=(
                            "AWSLambdaBasicExecutionRole is the minimum managed policy "
                            "required for Lambda CloudWatch Logs access."
                        ),
                    )
                ],
                apply_to_children=True,
            )

        # ---- Route 53 alias ------------------------------------------------
        route53.ARecord(
            self,
            "AliasRecord",
            zone=hosted_zone,
            record_name=domain_name,
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(distribution)
            ),
        )

        # ---- outputs -------------------------------------------------------
        CfnOutput(
            self, "DistributionDomain", value=distribution.distribution_domain_name
        )
        CfnOutput(self, "CustomDomain", value=f"https://{domain_name}")
        CfnOutput(self, "AlarmTopicArn", value=alarm_topic.topic_arn)
        if gateway_secrets:
            CfnOutput(self, "OriginVerifyHeader", value=ORIGIN_SECRET_HEADER)
        for i, gw in enumerate(plan.origin_verify_gateways):
            CfnOutput(
                self,
                f"OriginVerifySecret{i}Arn",
                value=gateway_secrets[gw].secret_arn,
                description=f"Origin-verify secret for {gw}",
            )
            CfnOutput(
                self,
                f"OriginVerifyInterceptor{i}Arn",
                value=interceptor_fns[gw].function_arn,
                description=f"Origin-verify REQUEST interceptor for {gw}",
            )
        for i, (route, endpoint, ep_plan) in enumerate(config.iter_plans()):
            CfnOutput(
                self,
                f"Endpoint{i}",
                value=f"https://{domain_name}{ep_plan.live_patterns[0]}",
                description=f"{endpoint.type} on route {route.path}",
            )

    def _build_web_acl(self) -> wafv2.CfnWebACL:
        def vis(metric):
            return wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=metric,
                sampled_requests_enabled=True,
            )

        return wafv2.CfnWebACL(
            self,
            "WebAcl",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            scope="CLOUDFRONT",
            visibility_config=vis("AgentCoreGatewayWaf"),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesCommonRuleSet",
                    priority=1,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS", name="AWSManagedRulesCommonRuleSet"
                        )
                    ),
                    visibility_config=vis("CommonRuleSet"),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesKnownBadInputsRuleSet",
                    priority=2,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesKnownBadInputsRuleSet",
                        )
                    ),
                    visibility_config=vis("KnownBadInputs"),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimit",
                    priority=3,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=2000, aggregate_key_type="IP"
                        )
                    ),
                    visibility_config=vis("RateLimit"),
                ),
            ],
        )
