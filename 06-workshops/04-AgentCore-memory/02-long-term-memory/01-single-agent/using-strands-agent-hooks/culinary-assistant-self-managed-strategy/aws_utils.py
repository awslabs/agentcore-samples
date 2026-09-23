import boto3
import json
import time
import uuid
import zipfile
import os
import io
from botocore.exceptions import ClientError


class AWSUtils:
    """Utility class for setting up AWS resources needed for AgentCore self-managed memory"""

    def __init__(self, region_name="us-east-1"):
        """Initialize with the AWS region to use"""
        self.region_name = region_name
        self.s3_client = boto3.client("s3", region_name=region_name)
        self.sns_client = boto3.client("sns", region_name=region_name)
        self.sqs_client = boto3.client("sqs", region_name=region_name)
        self.lambda_client = boto3.client("lambda", region_name=region_name)
        self.iam_client = boto3.client("iam", region_name=region_name)
        self.agentcore_client = boto3.client("bedrock-agentcore", region_name=region_name)
        self.agentcore_client_control = boto3.client("bedrock-agentcore-control", region_name=region_name)
        self.bedrock_runtime = boto3.client("bedrock-runtime", region_name=region_name)
        self.account_id = boto3.client("sts").get_caller_identity().get("Account")
        self.created_resources = {
            "s3_buckets": [],
            "sns_topics": [],
            "sqs_queues": [],
            "lambda_functions": [],
            "iam_roles": [],
            "memories": [],
        }

    # S3 Bucket Methods
    def create_s3_bucket(self, bucket_name_prefix):
        """Create an S3 bucket for AgentCore payloads"""
        bucket_name = f"{bucket_name_prefix}-{self.account_id}-{int(time.time())}"

        try:
            if self.region_name == "us-east-1":
                self.s3_client.create_bucket(Bucket=bucket_name)
            else:
                self.s3_client.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": self.region_name},
                )

            # Add lifecycle policy to delete objects after 7 days
            lifecycle_config = {
                "Rules": [
                    {
                        "Status": "Enabled",
                        "Prefix": "",
                        "Expiration": {"Days": 7},
                        "ID": "DeleteAfter7Days",
                    }
                ]
            }
            self.s3_client.put_bucket_lifecycle_configuration(
                Bucket=bucket_name, LifecycleConfiguration=lifecycle_config
            )

            print(f"Created S3 bucket: {bucket_name}")
            self.created_resources["s3_buckets"].append(bucket_name)
            return bucket_name

        except ClientError as e:
            print(f"Error creating S3 bucket: {e}")
            raise

    # SNS Topic Methods
    def create_sns_topic(self, topic_name):
        """Create an SNS topic for memory job notifications"""
        try:
            response = self.sns_client.create_topic(Name=topic_name)
            topic_arn = response["TopicArn"]
            print(f"Created SNS topic: {topic_arn}")
            self.created_resources["sns_topics"].append(topic_arn)
            return topic_arn

        except ClientError as e:
            print(f"Error creating SNS topic: {e}")
            raise

    # SQS Queue Methods
    def create_sqs_queue_with_sns_subscription(self, queue_name, sns_topic_arn):
        """Create SQS queue and subscribe it to SNS topic"""
        try:
            # Create SQS queue with visibility timeout higher than Lambda timeout (60 seconds)
            queue_response = self.sqs_client.create_queue(
                QueueName=queue_name,
                Attributes={
                    "VisibilityTimeout": "120"  # 120 seconds, double the Lambda timeout
                },
            )
            queue_url = queue_response["QueueUrl"]

            # Get queue ARN
            queue_attrs = self.sqs_client.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])
            queue_arn = queue_attrs["Attributes"]["QueueArn"]

            # Set queue policy to allow SNS
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "sns.amazonaws.com"},
                        "Action": "sqs:SendMessage",
                        "Resource": queue_arn,
                        "Condition": {"ArnEquals": {"aws:SourceArn": sns_topic_arn}},
                    }
                ],
            }

            self.sqs_client.set_queue_attributes(QueueUrl=queue_url, Attributes={"Policy": json.dumps(policy)})

            # Subscribe queue to SNS topic
            self.sns_client.subscribe(TopicArn=sns_topic_arn, Protocol="sqs", Endpoint=queue_arn)

            print(f"Created SQS queue: {queue_url} and subscribed to SNS topic")
            self.created_resources["sqs_queues"].append(queue_url)
            return queue_url, queue_arn

        except ClientError as e:
            print(f"Error setting up SQS queue: {e}")
            raise

    # IAM Role Methods
    def create_iam_role_for_agentcore(self, role_name, s3_bucket_name, sns_topic_arn):
        """Create IAM role for AgentCore to access S3 and SNS"""
        try:
            # Trust policy for AgentCore
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }

            # Create role
            create_role_response = self.iam_client.create_role(
                RoleName=role_name, AssumeRolePolicyDocument=json.dumps(trust_policy)
            )

            role_arn = create_role_response["Role"]["Arn"]

            # Create policy for S3 and SNS access
            policy_document = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "S3PayloadDelivery",
                        "Effect": "Allow",
                        "Action": ["s3:GetBucketLocation", "s3:PutObject"],
                        "Resource": [
                            f"arn:aws:s3:::{s3_bucket_name}",
                            f"arn:aws:s3:::{s3_bucket_name}/*",
                        ],
                    },
                    {
                        "Sid": "SNSNotifications",
                        "Effect": "Allow",
                        "Action": ["sns:GetTopicAttributes", "sns:Publish"],
                        "Resource": sns_topic_arn,
                    },
                ],
            }

            # Attach inline policy to role
            self.iam_client.put_role_policy(
                RoleName=role_name,
                PolicyName=f"{role_name}-policy",
                PolicyDocument=json.dumps(policy_document),
            )

            # Wait for IAM role to propagate
            print(f"Created IAM role: {role_arn}, waiting 10s for propagation...")
            time.sleep(10)

            self.created_resources["iam_roles"].append(role_name)
            return role_arn

        except ClientError as e:
            print(f"Error creating IAM role: {e}")
            raise

    def create_iam_role_for_lambda(self, role_name, s3_bucket_name, sqs_queue_arn):
        """Create IAM role for Lambda function"""
        try:
            # Trust policy for Lambda
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }

            # Create role
            create_role_response = self.iam_client.create_role(
                RoleName=role_name, AssumeRolePolicyDocument=json.dumps(trust_policy)
            )

            role_arn = create_role_response["Role"]["Arn"]

            # Attach AWSLambdaBasicExecutionRole for CloudWatch logs
            self.iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            )

            # Create policy for S3, SQS, and AgentCore access
            policy_document = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject"],
                        "Resource": [f"arn:aws:s3:::{s3_bucket_name}/*"],
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "sqs:ReceiveMessage",
                            "sqs:DeleteMessage",
                            "sqs:GetQueueAttributes",
                        ],
                        "Resource": sqs_queue_arn,
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "bedrock-agentcore:BatchCreateMemoryRecords",
                            "bedrock:InvokeModel",
                        ],
                        "Resource": "*",
                    },
                ],
            }

            # Attach inline policy to role
            self.iam_client.put_role_policy(
                RoleName=role_name,
                PolicyName=f"{role_name}-policy",
                PolicyDocument=json.dumps(policy_document),
            )

            # Wait for IAM role to propagate
            print(f"Created IAM role for Lambda: {role_arn}, waiting 10s for propagation...")
            time.sleep(10)

            self.created_resources["iam_roles"].append(role_name)
            return role_arn

        except ClientError as e:
            print(f"Error creating IAM role for Lambda: {e}")
            raise

    # Lambda Layer Method
    def create_boto3_layer(self, layer_name):
        """Create Lambda layer with the latest boto3"""
        try:
            # Create a temp directory for boto3 package
            import tempfile
            import subprocess
            import shutil

            # Create a temporary directory
            temp_dir = tempfile.mkdtemp()
            python_dir = os.path.join(temp_dir, "python")
            os.makedirs(python_dir)

            # Install boto3 to the temp directory
            subprocess.check_call(["pip", "install", "boto3", "--target", python_dir])

            # Create a zip file in memory
            layer_zip = os.path.join(temp_dir, "boto3_layer.zip")

            with zipfile.ZipFile(layer_zip, "w", zipfile.ZIP_DEFLATED) as zip_file:
                # Add all files from the python directory
                for root, _, files in os.walk(python_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        zip_file.write(file_path, os.path.relpath(file_path, temp_dir))

            # Upload the layer
            with open(layer_zip, "rb") as zip_file:
                response = self.lambda_client.publish_layer_version(
                    LayerName=layer_name,
                    Description="Layer with latest boto3 for AgentCore",
                    Content={"ZipFile": zip_file.read()},
                    CompatibleRuntimes=["python3.9"],
                )

            # Clean up
            shutil.rmtree(temp_dir)

            layer_version_arn = response["LayerVersionArn"]
            print(f"Created Lambda layer: {layer_version_arn}")
            return layer_version_arn

        except Exception as e:
            print(f"Error creating boto3 layer: {e}")
            raise

    # Lambda Function Methods
    def create_lambda_function(self, function_name, role_arn, handler_code, timeout=60, use_latest_boto3=True):
        """Create Lambda function for processing memory events"""
        try:
            # Create boto3 layer if requested
            layer_arn = None
            if use_latest_boto3:
                layer_name = f"boto3-layer-{int(time.time())}"
                layer_arn = self.create_boto3_layer(layer_name)

            # Create zip file in memory
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.writestr("lambda_function.py", handler_code)

            zip_buffer.seek(0)

            # Prepare function parameters
            function_params = {
                "FunctionName": function_name,
                "Runtime": "python3.9",
                "Role": role_arn,
                "Handler": "lambda_function.lambda_handler",
                "Code": {"ZipFile": zip_buffer.read()},
                "Timeout": timeout,
                "MemorySize": 256,
            }

            # Add layer if created
            if layer_arn:
                function_params["Layers"] = [layer_arn]

            # Create Lambda function
            response = self.lambda_client.create_function(**function_params)

            function_arn = response["FunctionArn"]
            print(f"Created Lambda function: {function_arn}")
            self.created_resources["lambda_functions"].append(function_name)
            return function_arn

        except ClientError as e:
            print(f"Error creating Lambda function: {e}")
            raise

    def add_sqs_trigger_to_lambda(self, function_name, sqs_queue_arn):
        """Add SQS as event source for Lambda function"""
        try:
            response = self.lambda_client.create_event_source_mapping(
                EventSourceArn=sqs_queue_arn,
                FunctionName=function_name,
                Enabled=True,
                BatchSize=1,
            )

            print(f"Added SQS trigger to Lambda function: {function_name}")
            return response["UUID"]

        except ClientError as e:
            print(f"Error adding SQS trigger to Lambda: {e}")
            raise

    # AgentCore Memory Methods
    def create_memory_with_self_managed_strategy(
        self,
        memory_name,
        memory_description,
        role_arn,
        sns_topic_arn,
        s3_bucket_name,
        strategy_name="SelfManagedMemory",
        message_trigger_count=5,
        token_trigger_count=1000,
        idle_timeout=900,  # 15 minutes
        historical_window_size=10,
    ):
        """Create memory with self-managed strategy"""
        try:
            client_token = str(uuid.uuid4())

            response = self.agentcore_client_control.create_memory(
                clientToken=client_token,
                name=memory_name,
                description=memory_description,
                memoryExecutionRoleArn=role_arn,
                eventExpiryDuration=7,  # 7 days
                memoryStrategies=[
                    {
                        "customMemoryStrategy": {
                            "name": strategy_name,
                            "description": "Custom self-managed memory strategy",
                            # 'namespaces': ['/interests/actor/{actorId}/session/{sessionId}/'],
                            "configuration": {
                                "selfManagedConfiguration": {
                                    "triggerConditions": [
                                        {"messageBasedTrigger": {"messageCount": message_trigger_count}},
                                        {"tokenBasedTrigger": {"tokenCount": token_trigger_count}},
                                        {"timeBasedTrigger": {"idleSessionTimeout": idle_timeout}},
                                    ],
                                    "invocationConfiguration": {
                                        "topicArn": sns_topic_arn,
                                        "payloadDeliveryBucketName": s3_bucket_name,
                                    },
                                    "historicalContextWindowSize": historical_window_size,
                                }
                            },
                        }
                    }
                ],
            )

            memory_id = response["memory"]["id"]
            # strategy_id = response['memory']['memoryStrategies'][0]['id']
            print(f"Created memory with ID: {memory_id}")

            self.created_resources["memories"].append(memory_id)
            return memory_id

        except ClientError as e:
            print(f"Error creating memory: {e}")
            raise

    # Event Creation Method for Testing
    def create_test_events(self, memory_id, actor_id="test-user", num_events=6):
        """Create test events to trigger the self-managed memory pipeline"""
        session_id = str(uuid.uuid4())

        print(f"Creating {num_events} test events for memory {memory_id}")

        for i in range(num_events):
            try:
                event_payload = [
                    {
                        "conversational": {
                            "content": {
                                "text": f"I like to eat {['pizza', 'sushi', 'tacos', 'pasta', 'burgers'][i % 5]} for dinner."
                            },
                            "role": "USER",
                        }
                    },
                    {
                        "conversational": {
                            "content": {
                                "text": f"I understand you like {['pizza', 'sushi', 'tacos', 'pasta', 'burgers'][i % 5]}. That's a great choice!"
                            },
                            "role": "ASSISTANT",
                        }
                    },
                ]

                self.agentcore_client.create_event(
                    memoryId=memory_id,
                    actorId=actor_id,
                    sessionId=session_id,
                    eventTimestamp=int(time.time()),
                    payload=event_payload,
                    clientToken=str(uuid.uuid4()),
                )

                print(f"Created event {i + 1}/{num_events}")

                # Small sleep to space out events
                time.sleep(1)

            except ClientError as e:
                print(f"Error creating test event: {e}")
                raise

        return session_id

    # Cleanup Method
    def cleanup_resources(self, prefix=None, discover_resources=True):
        """Clean up all resources created by this utility

        Args:
            prefix (str, optional): Prefix to filter resources by (e.g., 'agentcore-memory')
            discover_resources (bool, optional): Whether to try discovering resources if none are tracked
        """
        print("Starting cleanup of resources...")

        # Track deleted resources
        deleted_resources = 0
        total_resources = 0

        # Build resources to delete from tracked resources and/or discovery
        resources_to_delete = {k: list(v) for k, v in self.created_resources.items()}

        # If no tracked resources or discovery requested, try to find resources
        if discover_resources and sum(len(resources) for resources in self.created_resources.values()) == 0:
            print("No tracked resources found. Attempting to discover resources...")

            try:
                # Discover memories with name prefix 'SelfManageMemory'
                memory_prefix = "SelfManageMemory" if prefix is None else prefix
                memories = self.agentcore_client_control.list_memories(
                    filters=[{"key": "name", "value": memory_prefix, "operator": "CONTAINS"}]
                ).get("memorySummaries", [])

                for memory in memories:
                    if memory["id"] not in resources_to_delete["memories"]:
                        resources_to_delete["memories"].append(memory["id"])
                        print(f"Discovered memory: {memory['id']}")
            except Exception as e:
                print(f"Error discovering memories: {e}")

            try:
                # Discover Lambda functions
                lambda_prefix = "agentcore-memory-processor" if prefix is None else prefix
                functions = self.lambda_client.list_functions().get("Functions", [])
                for function in functions:
                    if (
                        lambda_prefix in function["FunctionName"]
                        and function["FunctionName"] not in resources_to_delete["lambda_functions"]
                    ):
                        resources_to_delete["lambda_functions"].append(function["FunctionName"])
                        print(f"Discovered Lambda function: {function['FunctionName']}")
            except Exception as e:
                print(f"Error discovering Lambda functions: {e}")

            try:
                # Discover SNS topics
                sns_prefix = "agentcore-memory-notifications" if prefix is None else prefix
                topics = self.sns_client.list_topics().get("Topics", [])
                for topic in topics:
                    if sns_prefix in topic["TopicArn"] and topic["TopicArn"] not in resources_to_delete["sns_topics"]:
                        resources_to_delete["sns_topics"].append(topic["TopicArn"])
                        print(f"Discovered SNS topic: {topic['TopicArn']}")
            except Exception as e:
                print(f"Error discovering SNS topics: {e}")

            try:
                # Discover SQS queues
                sqs_prefix = "agentcore-memory-queue" if prefix is None else prefix
                queues = self.sqs_client.list_queues(QueueNamePrefix=sqs_prefix).get("QueueUrls", [])
                for queue in queues:
                    if queue not in resources_to_delete["sqs_queues"]:
                        resources_to_delete["sqs_queues"].append(queue)
                        print(f"Discovered SQS queue: {queue}")
            except Exception as e:
                print(f"Error discovering SQS queues: {e}")

            try:
                # Discover IAM roles
                iam_prefixes = [
                    "AgentCoreMemoryExecutionRole",
                    "LambdaMemoryProcessingRole",
                ]
                if prefix is not None:
                    iam_prefixes = [prefix]

                roles = self.iam_client.list_roles().get("Roles", [])
                for role in roles:
                    role_name = role["RoleName"]
                    if (
                        any(prefix in role_name for prefix in iam_prefixes)
                        and role_name not in resources_to_delete["iam_roles"]
                    ):
                        resources_to_delete["iam_roles"].append(role_name)
                        print(f"Discovered IAM role: {role_name}")
            except Exception as e:
                print(f"Error discovering IAM roles: {e}")

            try:
                # Discover S3 buckets
                s3_prefix = "agentcore-memory-payloads" if prefix is None else prefix
                buckets = self.s3_client.list_buckets().get("Buckets", [])
                for bucket in buckets:
                    bucket_name = bucket["Name"]
                    if s3_prefix in bucket_name and bucket_name not in resources_to_delete["s3_buckets"]:
                        resources_to_delete["s3_buckets"].append(bucket_name)
                        print(f"Discovered S3 bucket: {bucket_name}")
            except Exception as e:
                print(f"Error discovering S3 buckets: {e}")

        # Check if there are any resources to clean up
        total_resources = sum(len(resources) for resources in resources_to_delete.values())
        if total_resources == 0:
            print("No resources to clean up. Make sure you've created resources first.")
            return

        # Delete memories
        for memory_id in resources_to_delete["memories"]:
            try:
                print(f"Deleting memory: {memory_id}")
                self.agentcore_client_control.delete_memory(memoryId=memory_id)
                print(f"Successfully deleted memory: {memory_id}")
                if memory_id in self.created_resources["memories"]:
                    self.created_resources["memories"].remove(memory_id)
                deleted_resources += 1
            except Exception as e:
                print(f"Error deleting memory {memory_id}: {e}")

        # Delete Lambda functions
        for function_name in resources_to_delete["lambda_functions"]:
            try:
                print(f"Deleting Lambda function: {function_name}")
                self.lambda_client.delete_function(FunctionName=function_name)
                print(f"Successfully deleted Lambda function: {function_name}")
                if function_name in self.created_resources["lambda_functions"]:
                    self.created_resources["lambda_functions"].remove(function_name)
                deleted_resources += 1
            except Exception as e:
                print(f"Error deleting Lambda function {function_name}: {e}")

        # Delete SQS queues
        for queue_url in resources_to_delete["sqs_queues"]:
            try:
                print(f"Deleting SQS queue: {queue_url}")
                self.sqs_client.delete_queue(QueueUrl=queue_url)
                print(f"Successfully deleted SQS queue: {queue_url}")
                if queue_url in self.created_resources["sqs_queues"]:
                    self.created_resources["sqs_queues"].remove(queue_url)
                deleted_resources += 1
            except Exception as e:
                print(f"Error deleting SQS queue {queue_url}: {e}")

        # Delete SNS topics
        for topic_arn in resources_to_delete["sns_topics"]:
            try:
                print(f"Deleting SNS topic: {topic_arn}")
                self.sns_client.delete_topic(TopicArn=topic_arn)
                print(f"Successfully deleted SNS topic: {topic_arn}")
                if topic_arn in self.created_resources["sns_topics"]:
                    self.created_resources["sns_topics"].remove(topic_arn)
                deleted_resources += 1
            except Exception as e:
                print(f"Error deleting SNS topic {topic_arn}: {e}")

        # Delete IAM roles
        for role_name in resources_to_delete["iam_roles"]:
            try:
                print(f"Deleting IAM role: {role_name}")
                # Detach all managed policies
                attached_policies = self.iam_client.list_attached_role_policies(RoleName=role_name)
                for policy in attached_policies.get("AttachedPolicies", []):
                    self.iam_client.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
                    print(f"Detached policy {policy['PolicyArn']} from role {role_name}")

                # Delete inline policies
                inline_policies = self.iam_client.list_role_policies(RoleName=role_name)
                for policy_name in inline_policies.get("PolicyNames", []):
                    self.iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
                    print(f"Deleted inline policy {policy_name} from role {role_name}")

                # Delete role
                self.iam_client.delete_role(RoleName=role_name)
                print(f"Successfully deleted IAM role: {role_name}")
                if role_name in self.created_resources["iam_roles"]:
                    self.created_resources["iam_roles"].remove(role_name)
                deleted_resources += 1
            except Exception as e:
                print(f"Error deleting IAM role {role_name}: {e}")

        # Delete S3 buckets - need to delete all objects first
        for bucket_name in resources_to_delete["s3_buckets"]:
            try:
                print(f"Deleting S3 bucket: {bucket_name} and its contents")
                # List and delete all objects
                objects = self.s3_client.list_objects_v2(Bucket=bucket_name)
                if "Contents" in objects:
                    for obj in objects["Contents"]:
                        self.s3_client.delete_object(Bucket=bucket_name, Key=obj["Key"])
                        print(f"Deleted object {obj['Key']} from bucket {bucket_name}")

                # Delete bucket
                self.s3_client.delete_bucket(Bucket=bucket_name)
                print(f"Successfully deleted S3 bucket: {bucket_name}")
                if bucket_name in self.created_resources["s3_buckets"]:
                    self.created_resources["s3_buckets"].remove(bucket_name)
                deleted_resources += 1
            except Exception as e:
                print(f"Error deleting S3 bucket {bucket_name}: {e}")

        print(f"Cleanup complete. Deleted {deleted_resources} out of {total_resources} resources.")
