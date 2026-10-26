"""Memory stack: one standalone AgentCore Memory resource for per-repo lessons.

Mirrors deploy/06_memory.sh. AWS::BedrockAgentCore::Memory is natively supported
in CloudFormation, so no custom resource is needed.

The orchestrator RECALLS lessons (retrieve_memory_records) before invoking the
coder and WRITES lessons (batch_create_memory_records) on finalize, namespaced
per repo as lessons/<repo>. The semantic strategy here enables semantic indexing
over those namespaces; {actorId} in the namespace template is bound to the repo id
at write/recall time (shared/memory.py uses lessons/<repo>).
"""
import aws_cdk as cdk
from constructs import Construct


class MemoryStack(cdk.Stack):
    """A single semantic AgentCore Memory over the lessons/<repo> namespaces."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Name pattern: ^[a-zA-Z][a-zA-Z0-9_]{0,47}$ — no hyphens.
        self.memory = cdk.CfnResource(
            self,
            "LessonsMemory",
            type="AWS::BedrockAgentCore::Memory",
            properties={
                "Name": f"{project}_lessons",
                "Description": f"Per-repo lessons learned for {project} coding agent",
                "EventExpiryDuration": 90,
                "MemoryStrategies": [
                    {
                        "SemanticMemoryStrategy": {
                            "Name": "repo_lessons",
                            "Namespaces": ["lessons/{actorId}"],
                        }
                    }
                ],
            },
        )
        # Memory is durable cross-ticket state — keep it on stack deletion.
        self.memory.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        # Exports consumed by the orchestrator stack.
        self.memory_id: str = cdk.Token.as_string(self.memory.get_att("MemoryId"))
        self.memory_arn: str = cdk.Token.as_string(self.memory.get_att("MemoryArn"))

        cdk.CfnOutput(
            self,
            "MemoryId",
            value=self.memory_id,
            export_name=f"{project}-memory-id",
        )
        cdk.CfnOutput(
            self,
            "MemoryArn",
            value=self.memory_arn,
            export_name=f"{project}-memory-arn",
        )
