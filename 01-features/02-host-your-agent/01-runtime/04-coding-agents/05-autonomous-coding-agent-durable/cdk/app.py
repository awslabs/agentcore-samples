#!/usr/bin/env python3
"""CDK app entry point for the event-driven autonomous coding agent system.

Stacks (dependency order):
  network -> storage -> build -> memory -> runtime -> orchestrator -> monitoring

`cdk deploy --all` stands up the entire system on a fresh account. The ONLY
out-of-band step is building + pushing the four ARM64 container images to ECR
(coding-agent, sandbox, sandbox-swift, evaluator) — done by the build stack's
CodeBuild projects (scripts/build_images.sh) or the shell scripts. See cdk/README.md.

The gateway_policy_stack.py (AgentCore Gateway + Cedar) is intentionally NOT wired
in — it was never part of the live shell-script deployment and is out of PoC scope.
Left in the tree for reference.
"""
import os

import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.storage_stack import StorageStack
from stacks.build_stack import BuildStack
from stacks.memory_stack import MemoryStack
from stacks.runtime_stack import RuntimeStack
from stacks.orchestrator_stack import OrchestratorStack
from stacks.monitoring_stack import MonitoringStack

app = cdk.App()

project: str = app.node.try_get_context("project") or "cagent"
region: str = app.node.try_get_context("region") or "us-east-1"
# Account resolved from: CDK context > CDK_DEFAULT_ACCOUNT env var > AWS caller identity
account: str = (
    app.node.try_get_context("account")
    or os.environ.get("CDK_DEFAULT_ACCOUNT", "")
    or None
)
if not account:
    raise ValueError(
        "AWS account not specified. Provide via: "
        "-c account=ACCOUNT_ID, or set CDK_DEFAULT_ACCOUNT env var, "
        "or ensure AWS credentials are configured."
    )

env = cdk.Environment(account=account, region=region)
common_tags = {"Project": project, "Environment": "production"}

# --- Stacks (ordered by dependency) ---

network = NetworkStack(app, f"{project}-network", project=project, env=env)

storage = StorageStack(
    app, f"{project}-storage",
    project=project,
    vpc=network.vpc,
    security_group=network.security_group,
    private_subnets=network.private_subnets,
    env=env,
)
storage.add_dependency(network)

build = BuildStack(app, f"{project}-build", project=project, bucket=storage.bucket, env=env)
build.add_dependency(storage)

memory = MemoryStack(app, f"{project}-memory", project=project, env=env)

runtime = RuntimeStack(
    app, f"{project}-runtime",
    project=project,
    vpc=network.vpc,
    security_group=network.security_group,
    private_subnets=network.private_subnets,
    access_point_arn=storage.access_point_arn,
    env=env,
)
runtime.add_dependency(storage)
runtime.add_dependency(build)  # ECR repos must exist; images pushed before this deploys

orchestrator = OrchestratorStack(
    app, f"{project}-orchestrator",
    project=project,
    bucket=storage.bucket,
    memory_id=memory.memory_id,
    coding_agent_arn=runtime.coding_agent_arn,
    sandbox_arn=runtime.sandbox_arn,
    sandbox_swift_arn=runtime.sandbox_swift_arn,
    evaluator_arn=runtime.evaluator_arn,
    env=env,
)
orchestrator.add_dependency(runtime)
orchestrator.add_dependency(memory)

monitoring = MonitoringStack(
    app, f"{project}-monitoring",
    project=project,
    lambda_fn=orchestrator.lambda_fn,
    sns_topic=orchestrator.sns_topic,
    env=env,
)
monitoring.add_dependency(orchestrator)

for stack in [network, storage, build, memory, runtime, orchestrator, monitoring]:
    for key, value in common_tags.items():
        cdk.Tags.of(stack).add(key, value)

app.synth()
