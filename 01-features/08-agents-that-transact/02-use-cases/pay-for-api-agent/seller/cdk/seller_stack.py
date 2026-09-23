"""Fun Facts seller CDK stack.

Mirrors the agentcore-payments house-standard seller pattern
(backend/lambdas/sellers/crypto-price):

  - Node.js 20 ARM64 AWS Lambda function with pre-installed
    ``node_modules`` packaged into the asset (the deploy script runs
    ``npm install`` before ``cdk deploy``).
  - Two env vars for payout — ``SELLER_WALLET_ADDRESS`` (EVM / Base
    Sepolia) and ``SELLER_SOLANA_WALLET_ADDRESS`` (Solana / Devnet). Both
    are forwarded by the x402 seller library into the ``accepts`` array on
    each 402 response. Set one, both, or neither; the Lambda emits one
    ``accepts`` entry per configured network.
  - ``X402_FACILITATOR_URL`` — override to point at a private facilitator.
    Defaults to the public x402.org facilitator.
  - One route: ``GET /facts`` behind the x402 payment middleware, plus
    public ``GET /`` and ``GET /health`` for sanity checks.
"""

from __future__ import annotations

import os
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
)
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from constructs import Construct

LAMBDA_CODE_DIR = str(Path(__file__).resolve().parent.parent / "lambda")


class AgentCorePaymentsFunFactsSellerStack(Stack):
    """A minimal x402 seller: HTTP API → Node.js Lambda."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Seller config ────────────────────────────────────────────
        # Override via CDK context (`cdk deploy -c seller_wallet=0x…`) or
        # environment variables at deploy time. Both networks are optional;
        # if neither is set the Lambda still runs but the facilitator will
        # reject payment proofs. Set at least one.
        #
        # Defaults to "WALLET_NOT_CONFIGURED" (mirrors seller/lambda/index.js)
        # so an unset wallet shows up as a clearly invalid placeholder
        # rather than an empty string.
        evm_wallet = (
            self.node.try_get_context("seller_wallet")
            or os.environ.get("SELLER_WALLET_ADDRESS")
            or "WALLET_NOT_CONFIGURED"
        )
        solana_wallet = (
            self.node.try_get_context("seller_solana_wallet")
            or os.environ.get("SELLER_SOLANA_WALLET_ADDRESS")
            or "WALLET_NOT_CONFIGURED"
        )
        facilitator_url = os.environ.get("X402_FACILITATOR_URL") or "https://x402.org/facilitator"
        price = os.environ.get("X402_PRICE") or "$0.01"

        # ── Lambda function ──────────────────────────────────────────
        seller_fn = _lambda.Function(
            self,
            "SellerFunction",
            runtime=_lambda.Runtime.NODEJS_20_X,
            architecture=_lambda.Architecture.ARM_64,
            handler="index.handler",
            # The deploy script runs `npm install` in the lambda/ folder
            # before `cdk deploy` so the asset ships node_modules inline.
            # Matches the pattern used by agentcore-payments sellers.
            code=_lambda.Code.from_asset(LAMBDA_CODE_DIR),
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={
                "SELLER_WALLET_ADDRESS": evm_wallet,
                "SELLER_SOLANA_WALLET_ADDRESS": solana_wallet,
                "X402_FACILITATOR_URL": facilitator_url,
                "X402_PRICE": price,
            },
            log_retention=logs.RetentionDays.ONE_WEEK,
            description="Fun Facts x402 seller — AgentCore Payments use case",
        )

        # ── HTTP API ─────────────────────────────────────────────────
        # CORS is wide-open for the demo so the seller is reachable from
        # any caller (the AgentCore Runtime container, a browser-based
        # debugger, a curl session). For production, restrict origins to
        # the specific agent runtime endpoints that need to call this
        # seller, and limit methods to GET + OPTIONS.
        http_api = apigwv2.HttpApi(
            self,
            "SellerHttpApi",
            api_name="pay-for-api-fun-facts",
            description="Fun Facts x402 seller — pay-per-fact via x402",
            cors_preflight=apigwv2.CorsPreflightOptions(
                # Demo configuration — restrict to specific origins in
                # production (for example, your agent runtime domains).
                allow_origins=["*"],
                allow_methods=[apigwv2.CorsHttpMethod.ANY],
                allow_headers=["*"],
            ),
        )

        integration = apigwv2_integrations.HttpLambdaIntegration(
            "SellerLambdaIntegration",
            handler=seller_fn,
        )

        # Single proxy route catches GET /, GET /facts, GET /health.
        http_api.add_routes(
            path="/{proxy+}",
            methods=[apigwv2.HttpMethod.ANY],
            integration=integration,
        )
        http_api.add_routes(
            path="/",
            methods=[apigwv2.HttpMethod.ANY],
            integration=integration,
        )

        # ── Outputs ──────────────────────────────────────────────────
        CfnOutput(
            self,
            "SellerApiUrl",
            value=http_api.api_endpoint,
            description="Invoke URL for the Fun Facts x402 seller API",
        )
        CfnOutput(
            self,
            "SellerEvmWallet",
            value=evm_wallet or "(unset)",
            description=(
                "EVM (Base Sepolia) wallet that receives USDC for paid "
                "requests. Set via `cdk deploy -c seller_wallet=0x…` or "
                "the SELLER_WALLET_ADDRESS env var."
            ),
        )
        CfnOutput(
            self,
            "SellerSolanaWallet",
            value=solana_wallet or "(unset)",
            description=(
                "Solana (Devnet) wallet that receives USDC for paid "
                "requests. Set via `cdk deploy -c seller_solana_wallet=…` "
                "or the SELLER_SOLANA_WALLET_ADDRESS env var."
            ),
        )
