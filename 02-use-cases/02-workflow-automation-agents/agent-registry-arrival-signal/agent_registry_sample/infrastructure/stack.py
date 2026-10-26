# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U1 infrastructure: the CDK (Python) stack provisioning the surrounding
AWS resources for the arrival-signal sample.

Construct build order:
    KMS keys -> messaging (queue/DLQ/topic) -> DynamoDB table ->
    trigger-adapter Lambda+role+grants -> publisher IAM roles ->
    EventBridge rule+targets (last, since it references the queue/topic).

Two CDK behaviors in aws-cdk-lib 2.274.0 that this stack relies on:
  (a) aws_bedrockagentcore.Runtime/IBedrockAgentRuntime has a dedicated
      ``grant_invoke_runtime()`` method that grants only
      ``bedrock-agentcore:InvokeAgentRuntime``. The broader ``grant_invoke()``
      also grants ``InvokeAgentRuntimeForUser``. The stack uses the narrow
      method instead of a hand-written PolicyStatement.
  (b) aws_events_targets.SqsQueue / SnsTopic add the EventBridge service
      principal's KMS grant (kms:GenerateDataKey*, kms:Decrypt) to a
      KMS-encrypted queue/topic's key policy when added as a rule target, so
      the stack makes no kms.Key.grant() call for the EventBridge principal.
"""

from __future__ import annotations

import os

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_bedrockagentcore as bedrockagentcore
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as lambda_event_sources
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sqs as sqs
from constructs import Construct

# Registry control-plane actions granted to the publisher (U4) IAM roles. This
# stack never calls these APIs. Per the AWS Agent Registry service-authorization
# reference, each action is authorized at a specific resource level. The two
# resource types are `registry` (arn .../registry/<id>) and `registry-record`
# (arn .../registry/<id>/record/<id>):
#   CreateRegistryRecord            -> registry level (the record does not
#                                      exist yet, so it is authorized on the
#                                      parent).
#   SubmitRegistryRecordForApproval -> registry-record level.
#   UpdateRegistryRecordStatus      -> registry-record level.
# Scoping a record-level action to the registry ARN fails with a silent
# AccessDenied (create succeeds, submit is denied).
_REGISTRY_CREATE_ACTION = "agent-registry:CreateRegistryRecord"
_REGISTRY_SUBMIT_ACTION = "agent-registry:SubmitRegistryRecordForApproval"
_REGISTRY_APPROVE_ACTION = "agent-registry:UpdateRegistryRecordStatus"

# The EventBridge Approved-event pattern.
_APPROVED_EVENT_SOURCE = "aws.agent-registry"
_APPROVED_EVENT_DETAIL_TYPE = "Registry Record State changed to Approved"

_DLQ_MAX_RECEIVE_COUNT = 3

# A cold orchestrator invocation can take well over 10s, so the adapter Lambda
# gets 60s. The SQS visibility timeout stays at 6x the Lambda timeout so a slow
# but live invocation is not redelivered while still in flight.
_ADAPTER_LAMBDA_TIMEOUT = Duration.seconds(60)
_CONSUMER_QUEUE_VISIBILITY_TIMEOUT = Duration.seconds(360)  # 6x the Lambda timeout

# TTL window for the idempotency table's demo records.
_IDEMPOTENCY_TABLE_TTL_DAYS = 7


class InfrastructureStack(Stack):
    """U1 infrastructure: EventBridge Approved-event fan-out, SQS+DLQ, SNS,
    DynamoDB idempotency table, the logic-free trigger-adapter Lambda, the
    three least-privilege IAM roles, and two customer-managed KMS keys.

    ``orchestrator_agent_runtime_arn``, ``orchestrator_agent_runtime_id``,
    and ``orchestrator_agent_runtime_role_arn`` reference U2's orchestrator
    AgentCore Runtime, imported via ``Runtime.from_agent_runtime_attributes``
    because U2 deploys separately. ``registry_arn`` is the AWS Agent Registry
    registry resource the publisher (U4) operates against.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        orchestrator_agent_runtime_arn: str,
        orchestrator_agent_runtime_id: str,
        orchestrator_agent_runtime_role_arn: str,
        registry_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self._orchestrator_runtime = bedrockagentcore.Runtime.from_agent_runtime_attributes(
            self,
            "OrchestratorRuntime",
            agent_runtime_arn=orchestrator_agent_runtime_arn,
            agent_runtime_id=orchestrator_agent_runtime_id,
            agent_runtime_name="orchestrator",
            role_arn=orchestrator_agent_runtime_role_arn,
        )
        self._registry_arn = registry_arn
        self._orchestrator_role_arn = orchestrator_agent_runtime_role_arn

        self._create_kms_keys()
        self._create_messaging()
        self._create_dynamo_table()
        self._create_trigger_adapter()
        self._create_publisher_roles()
        self._grant_orchestrator_runtime_access()
        self._create_event_rule()

        self._create_outputs()

    # Step 1: KMS keys

    def _create_kms_keys(self) -> None:
        """Two separate customer-managed KMS keys, never shared and never
        AWS-managed. The EventBridge service principal's kms:GenerateDataKey*
        grant comes from aws_events_targets.SqsQueue/SnsTopic when the
        encrypted queue/topic is added as a rule target, so no grant call for
        that principal is made here.
        """
        self.sqs_kms_key = kms.Key(
            self,
            "SqsKmsKey",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
            description="Encrypts the Approved-event consumer queue and its DLQ (U1).",
        )
        self.sns_kms_key = kms.Key(
            self,
            "SnsKmsKey",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
            description="Encrypts the Approved-event notification topic (U1).",
        )

    # Step 2: messaging

    def _create_messaging(self) -> None:
        """Standard (not FIFO) consumer queue with its own DLQ, plus the SNS
        notification topic. The queue and topic are the EventBridge rule's two
        targets.
        """
        self.dead_letter_queue = sqs.Queue(
            self,
            "SqsDeadLetterQueue",
            encryption_master_key=self.sqs_kms_key,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.consumer_queue = sqs.Queue(
            self,
            "SqsConsumerQueue",
            encryption_master_key=self.sqs_kms_key,
            visibility_timeout=_CONSUMER_QUEUE_VISIBILITY_TIMEOUT,
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=_DLQ_MAX_RECEIVE_COUNT,
                queue=self.dead_letter_queue,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.notification_topic = sns.Topic(
            self,
            "SnsNotificationTopic",
            master_key=self.sns_kms_key,
        )

    # Step 3: DynamoDB idempotency table

    def _create_dynamo_table(self) -> None:
        """Composite key (recordId, state) so a downstream conditional put
        (done by the orchestrator in U5, not by U1) can dedupe safely.
        On-demand billing. TTL bounds demo-record growth.

        The table name is derived from the registry id
        (naming.idempotency_table_name). The orchestrator runtime deploys
        before this stack, so it gets the same name as its
        IDEMPOTENCY_TABLE_NAME env var without a cross-stack reference.
        """
        from agent_registry_sample.naming import (
            idempotency_table_name,
            registry_id_from_arn,
        )

        table_name = idempotency_table_name(registry_id_from_arn(self._registry_arn))
        self.idempotency_table = dynamodb.TableV2(
            self,
            "DynamoIdempotencyTable",
            table_name=table_name,
            partition_key=dynamodb.Attribute(name="recordId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="state", type=dynamodb.AttributeType.STRING),
            time_to_live_attribute="expiresAt",
            removal_policy=RemovalPolicy.DESTROY,
        )
        # checkov:skip=CKV_AWS_165: point-in-time recovery is intentionally
        # omitted because this demo table has a 7-day TTL and holds an
        # idempotency ledger for a sample walkthrough, not business data.
        self.idempotency_table.node.default_child.add_metadata(
            "checkov",
            {
                "skip": [
                    {
                        "id": "CKV_AWS_165",
                        "comment": ("No PITR by design for this 7-day-TTL demo idempotency table."),
                    }
                ]
            },
        )

    # Step 4: trigger-adapter Lambda + role

    def _create_trigger_adapter(self) -> None:
        """Builds TriggerAdapterRole first (CDK Lambda constructs take a
        `role` prop at construction time), grants it only the permissions it
        needs via scoped grant*() calls, then builds the Lambda. This stack
        wires the shell (runtime, timeout, event source, role). The
        logic-free parse-and-invoke handler ships as a Lambda asset
        (adapter_src/).
        """
        self.trigger_adapter_role = iam.Role(
            self,
            "TriggerAdapterRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Logic-free execution role for the SQS trigger adapter.",
        )
        # Receive/delete/get-attributes on this queue only. The ARN comes from
        # the construct, not a hand-typed string.
        self.consumer_queue.grant_consume_messages(self.trigger_adapter_role)
        # Decrypt on SqsKmsKey only, never SnsKmsKey.
        self.sqs_kms_key.grant_decrypt(self.trigger_adapter_role)
        # Behavior (a): grants only bedrock-agentcore:InvokeAgentRuntime, not
        # InvokeAgentRuntimeForUser.
        self._orchestrator_runtime.grant_invoke_runtime(self.trigger_adapter_role)
        # This role gets no DynamoDB and no agent-registry permissions.
        # AWSLambdaBasicExecutionRole grants CreateLogGroup/CreateLogStream/
        # PutLogEvents scoped to this function's own log group via Lambda's
        # standard log-group ARN convention. No broader logs:* grant is added.
        self.trigger_adapter_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )

        # CKV_AWS_173: Lambda environment variables are encrypted with a
        # dedicated customer-managed key as defense in depth. The one variable
        # (the orchestrator's ARN) is a non-secret identifier, but encrypting
        # it costs nothing and shows the stronger default.
        self.lambda_env_kms_key = kms.Key(
            self,
            "LambdaEnvKmsKey",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
            description="Encrypts TriggerAdapterLambda's environment variables.",
        )

        self.trigger_adapter_lambda = lambda_.Function(
            self,
            "TriggerAdapterLambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.on_message",
            # The handler ships as a code asset. Inline Lambda code is named
            # index.py, so handler="handler.on_message" would raise
            # Runtime.ImportModuleError and every Approved event would
            # dead-letter. adapter_src/ contains only handler.py, so the asset
            # is the module the handler= string names.
            code=lambda_.Code.from_asset(os.path.join(os.path.dirname(__file__), "adapter_src")),
            timeout=_ADAPTER_LAMBDA_TIMEOUT,
            role=self.trigger_adapter_role,
            environment={
                "ORCHESTRATOR_AGENT_RUNTIME_ARN": self._orchestrator_runtime.agent_runtime_arn,
            },
            environment_encryption=self.lambda_env_kms_key,
            # CKV_AWS_115: a reserved-concurrency limit bounds this function's
            # blast radius and cost under a traffic spike. 5 is generous for a
            # low-traffic demo trigger.
            reserved_concurrent_executions=5,
        )
        self.lambda_env_kms_key.grant_encrypt_decrypt(self.trigger_adapter_role)

        # checkov:skip=CKV_AWS_117: no VPC by design. Every resource this
        # Lambda talks to (SQS, KMS, bedrock-agentcore) is a public AWS service
        # endpoint, and U1 has no RDS, ElastiCache, or private subnet
        # dependency.
        #
        # checkov:skip=CKV_AWS_116: this check looks for a Lambda-level
        # DeadLetterConfig, which applies to asynchronous invocation failures.
        # TriggerAdapterLambda is SQS-triggered (synchronous, poll-based), so
        # SqsConsumerQueue's own redrive policy (maxReceiveCount=3 ->
        # SqsDeadLetterQueue) handles a failed invocation before any
        # Lambda-level async-failure destination would apply.
        self.trigger_adapter_lambda.node.default_child.add_metadata(
            "checkov",
            {
                "skip": [
                    {
                        "id": "CKV_AWS_117",
                        "comment": "No VPC needed; dependencies are public AWS service endpoints.",
                    },
                    {
                        "id": "CKV_AWS_116",
                        "comment": (
                            "SQS-triggered (sync poll), not async-invoked; "
                            "the SQS consumer queue's own redrive policy "
                            "is the correct DLQ mechanism here, "
                            "not a Lambda-level async DeadLetterConfig."
                        ),
                    },
                ]
            },
        )
        self.trigger_adapter_lambda.add_event_source(
            lambda_event_sources.SqsEventSource(self.consumer_queue, batch_size=1)
        )

    # Step 5: publisher IAM roles

    def _create_publisher_roles(self) -> None:
        """Two distinct roles for U4's create/submit and approve actions.
        They have no construct dependency on the messaging/compute resources
        above. How the publisher (U4) assumes them (sts:AssumeRole or a
        separate profile) is up to U4.
        """
        # Record-level actions are scoped to the record ARN
        # (registry/<id>/record/*), not the registry ARN.
        record_arn = f"{self._registry_arn}/record/*"

        self.publisher_submitter_role = iam.Role(
            self,
            "RegistryPublisherSubmitterRole",
            assumed_by=iam.AccountPrincipal(self.account),
            description="Create + submit only -- cannot self-approve.",
        )
        # Create is a registry-level action because the record does not exist yet.
        self.publisher_submitter_role.add_to_policy(
            iam.PolicyStatement(
                actions=[_REGISTRY_CREATE_ACTION],
                resources=[self._registry_arn],
            )
        )
        # Submit is a record-level action on the created record.
        self.publisher_submitter_role.add_to_policy(
            iam.PolicyStatement(
                actions=[_REGISTRY_SUBMIT_ACTION],
                resources=[record_arn],
            )
        )

        self.publisher_approver_role = iam.Role(
            self,
            "RegistryPublisherApproverRole",
            assumed_by=iam.AccountPrincipal(self.account),
            description="Approve only -- cannot create or submit.",
        )
        # UpdateRegistryRecordStatus is a record-level action.
        self.publisher_approver_role.add_to_policy(
            iam.PolicyStatement(
                actions=[_REGISTRY_APPROVE_ACTION],
                resources=[record_arn],
            )
        )

    # Step 5b: orchestrator runtime grants

    def _grant_orchestrator_runtime_access(self) -> None:
        """Attach the orchestrator's own runtime data-plane grants to its
        execution role. These are BatchGet + Search on the registry (the U5
        event path and U6 search path) and PutItem/UpdateItem/GetItem on the
        idempotency table (the U5 two-phase dedupe).

        The grants live in this stack, not the orchestrator's, because the
        orchestrator AgentCore Runtime deploys before this stack (this stack
        imports it). The dedupe grant references the DynamoDB table this stack
        creates, which does not exist at the orchestrator's deploy time, so
        granting it there would be a circular dependency. This stack already
        holds the orchestrator role ARN, the table, and the registry ARN. The
        orchestrator's identity policy grants no InvokeAgentRuntime, because
        each peer's resource-based policy authorizes the A2A invoke (the
        consumer-group model).

        No control-plane action (CreateRegistryRecord etc.) is added here. The
        walkthrough's registration step registers the orchestrator at deploy
        time under the operator's own credentials, not the runtime at
        invocation time.
        """
        orchestrator_role = iam.Role.from_role_arn(
            self,
            "ImportedOrchestratorRole",
            self._orchestrator_role_arn,
            # Same account, so addToPrincipalPolicy attaches a new standalone
            # AWS::IAM::Policy to the existing role. mutable=True is the
            # default and is stated for intent.
            mutable=True,
        )
        # Data-plane reads, at two different resource levels:
        #   Search -> registry level (searches within the registry, U6).
        #   GetDiscoverableRegistryRecord -> record level. This IAM action
        #     authorizes the BatchGetDiscoverableRegistryRecord API the event
        #     path (U5) uses. There is no
        #     agent-registry:BatchGetDiscoverableRegistryRecord IAM action, and
        #     granting that name results in a silent AccessDenied.
        # No control-plane action, no InvokeAgentRuntime (the peer resource
        # policy authorizes A2A), and no wildcard resource.
        orchestrator_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["agent-registry:SearchDiscoverableRegistryRecords"],
                resources=[self._registry_arn],
            )
        )
        orchestrator_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["agent-registry:GetDiscoverableRegistryRecord"],
                resources=[f"{self._registry_arn}/record/*"],
            )
        )
        # Two-phase dedupe (U5), scoped to U1's idempotency table only. This is
        # an identity policy on the imported orchestrator role, not
        # table.grant(). On an imported role, table.grant() attaches a
        # resource-based policy to the TableV2 that references the table's own
        # ARN (Fn::GetAtt). That creates a self-referential dependency cycle
        # (DynamoIdempotencyTable -> DynamoIdempotencyTable) and makes the
        # template undeployable. add_to_principal_policy keeps the grant on the
        # role side, so only the role's policy depends on the table.
        orchestrator_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:GetItem",
                ],
                resources=[self.idempotency_table.table_arn],
            )
        )

    # Step 6: EventBridge rule + two targets

    def _create_event_rule(self) -> None:
        """One rule that matches only the Approved transition (never Pending
        Approval or any other status) and fans out to the two targets through
        aws_events_targets' dedicated classes. Those classes also add each
        target's KMS grant (behavior (b)) and the queue's EventBridge-publish
        resource policy.
        """
        self.approved_event_rule = events.Rule(
            self,
            "ApprovedEventRule",
            event_pattern=events.EventPattern(
                source=[_APPROVED_EVENT_SOURCE],
                detail_type=[_APPROVED_EVENT_DETAIL_TYPE],
            ),
        )
        self.approved_event_rule.add_target(targets.SqsQueue(self.consumer_queue))
        self.approved_event_rule.add_target(targets.SnsTopic(self.notification_topic))

    # Outputs

    def _create_outputs(self) -> None:
        CfnOutput(self, "ConsumerQueueUrl", value=self.consumer_queue.queue_url)
        CfnOutput(self, "DeadLetterQueueUrl", value=self.dead_letter_queue.queue_url)
        CfnOutput(self, "NotificationTopicArn", value=self.notification_topic.topic_arn)
        CfnOutput(self, "IdempotencyTableName", value=self.idempotency_table.table_name)
        CfnOutput(self, "PublisherSubmitterRoleArn", value=self.publisher_submitter_role.role_arn)
        CfnOutput(self, "PublisherApproverRoleArn", value=self.publisher_approver_role.role_arn)
