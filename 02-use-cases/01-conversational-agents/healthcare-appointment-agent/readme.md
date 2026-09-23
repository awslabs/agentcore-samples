# Healthcare Appointment Agent

## Overview
An AI agent for immunization related healthcare appointments built with **Amazon Bedrock AgentCore Gateway** using the **Model Context Protocol (MCP)** to expose the tools. This AI agent supports enquiring about current immunization status/schedule, checking appointment slots and booking appointments. It also provides personalized experience by knowing the logged in user (adult) and his/her children and uses **AWS Healthlake** as **FHIR R4** (Fast Healthcare Interoperability Resources) database.

### Use case details
| Information         | Details                                                                                                                             |
|---------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| Use case type       | Conversational                                                                                                                      |
| Agent type          | Single Agent                                                                                                                        |
| Use case components | Amazon Bedrock AgentCore related components: Gateway, Identity, and Policy (optional)                                              |
|  					  | Other Components: Amazon Cognito, AWS Healthlake, Amazon API Gateway and AWS Lambda                                                 |
|  					  | MCP Tools: MCP tools are exposed to Bedrock AgentCore Gateway using OpenAPI specification                                          |
| Use case vertical   | Healthcare                                                                                                                          |
| Example complexity  | Intermediate                                                                                                                        |
| SDK used            | Amazon Bedrock AgentCore SDK and boto3								                                                                |


### Use case Architecture
![Image1](static/healthcare_gateway_flow.png)

### Use case key Features
- **Patient-Scoped Access Control**: Optional AgentCore Policy integration ensures patients can only access their own healthcare data
- **FHIR R4 Compliance**: Uses AWS HealthLake as a FHIR-compliant healthcare database
- **Immunization Management**: Check immunization status, schedules, and pending vaccinations
- **Appointment Booking**: Search available slots and book appointments
- **Identity-Aware**: Knows the logged-in user and their family members
- **Multi-Framework Support**: Works with both Strands and LangGraph agents

## Prerequisites
**Note: These steps are designed to work in us-east-1 and us-west-2 regions.**

### Required IAM Policies
Please ensure the required IAM permissions. Ignore if running this sample from Admin role.

Cloudformation stack used in this sample has AWS Healthlake, Cognito, S3, IAM Roles, API Gateway, Lambda functions related sources.

As a quick start, you may use the combination of AWS managed IAM policies and an Inline policy to avoid issues in deploying and setting up this code sample. However it is recommended to follow the principle of privilege in production.

**AWS managed IAM policies:**
* AmazonAPIGatewayAdministrator
* AmazonCognitoPowerUser
* AmazonHealthLakeFullAccess
* AmazonS3FullAccess
* AWSCloudFormationFullAccess
* AWSKeyManagementServicePowerUser
* AWSLakeFormationDataAdmin
* AWSLambda_FullAccess
* AWSResourceAccessManagerFullAccess
* CloudWatchFullAccessV2
* AmazonBedrockFullAccess

**Inline Policy:**
```
{
	"Version": "2012-10-17",
	"Statement": [
		{
			"Effect": "Allow",
			"Action": [
				"s3:GetObject",
				"s3:PutObject"
			],
			"Resource": [
				"arn:aws:s3:::amzn-s3-demo-source-bucket/*",
				"arn:aws:s3:::amzn-s3-demo-logging-bucket/*"
			]
		},
		{
			"Effect": "Allow",
			"Action": [
				"ram:GetResourceShareInvitations",
				"ram:AcceptResourceShareInvitation",
				"glue:CreateDatabase",
				"glue:DeleteDatabase"
			],
			"Resource": "*"
		},
		{
			"Effect": "Allow",
			"Action": [
				"bedrock-agentcore:*",
				"agent-credential-provider:*"
			],
			"Resource": "*"
		}
	]
}
```
### Others
* Python 3.12
* GIT
* AWS CLI 2.x
* Claude 3.5 Sonnet model enabled on Amazon Bedrock. Please follow this [guide](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access-modify.html) to set up the same.

## Use case Setup
Clone the GIT repository and navigate the the Healthcare-Appointment-Agent directory.

```
git clone <repository-url>
cd ./02-use-cases/05-Healthcare-Appointment-Agent/
```

### Setup Infrastructure
Create an S3 bucket (**ignore if you would like to use an existing bucket**)

```
aws s3api create-bucket --bucket <globally unique bucket name here>
```

Push the lambda zip package to S3 bucket
```
aws s3 cp "./cloudformation/fhir-openapi-searchpatient.zip" s3://<bucket name here>/lambda_code/fhir-openapi-searchpatient.zip
```

Deploy cloudformation template by using below steps. The stack will take around 10 minutes. You can monitor the progress of stack by following this [guide](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/monitor-stack-progress.html).
```
aws cloudformation create-stack \
  --stack-name healthcare-cfn-stack \
  --template-body file://cloudformation/healthcare-cfn.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --region <us-east-1 or us-west-2> \
  --parameters ParameterKey=LambdaS3Bucket,ParameterValue="<bucket name here>" \
               ParameterKey=LambdaS3Key,ParameterValue="lambda_code/fhir-openapi-searchpatient.zip"
```

### Install python dependencies and initialize the environment
Install UV as per this [guide](https://docs.astral.sh/uv/getting-started/installation/)

Create and activate virtual environment

```
uv venv --python 3.12
source ./.venv/bin/activate
```

Install dependencies

```
uv pip install -r requirements.txt
```

Initialize the environment by running below command. This will create an **.env** file which would be used for environment variables. Use the same region name as what was used with Cloudformation template above. Note down **APIEndpoint** and **APIGWCognitoLambdaName** as returned in the output.

```
python init_env.py \
--cfn_name healthcare-cfn-stack \
--openapi_spec_file ./fhir-openapi-spec.yaml \
--region <us-east-1 or us-west-2>
```

if you need to use a named credential profile then same can be achieved with below.

```
python init_env.py \
--cfn_name healthcare-cfn-stack \
--region <us-east-1 or us-west-2> \
--openapi_spec_file ./fhir-openapi-spec.yaml \
--profile <profile-name here>>
```

The **.env** file should look like below.
![EnvImage1](static/env_screenshot1.png)


**Enable Cognito Auth with API Gateway**
```
aws lambda invoke \
--function-name <input APIGWCognitoLambdaName as noted earlier> \
response.json \
--payload '{ "RequestType": "Create" }' \
--cli-binary-format raw-in-base64-out \
--region <us-east-1 or us-west-2>
```

### Create some test data in AWS Healthlake
Run the below python program to ingest the test data as present in **test_data** folder. It may take around ~5 minutes to complete.
```
python create_test_data.py
```

## Execution Instructions
### Create Bedrock AgentCore Gateway and Gateway Target
Open the OpenAPI spec file **fhir-openapi-spec.yaml** and replace **<your API endpoint here>** with **APIEndpoint** as noted down earlier.

Set up Bedrock AgentCore Gateway and Gateway Target based on OpenAPI specification in **fhir-openapi-spec.yaml** file. Note down the Gaeway Id from the output as it would be needed in later steps.

```
python setup_fhir_mcp.py --op_type Create --gateway_name <gateway_name_here>
```

### Run Strands Agent
Run Strands Agent by using below steps.

```
python strands_agent.py --gateway_id <gateway_id_here>
```

### Run Langgraph Agent
Run Strands Agent by using below steps.

```
python langgraph_agent.py --gateway_id <gateway_id_here>
```

### Sample prompts to interact with Agent:
* How can you help?
* Let us check for immunization schedule first
* Please find slots for MMR vaccine around the scheduled date

![Image1](static/appointment_agent_demo.gif)

## Optional: Add Policy-Based Access Control

To add patient-scoped access control using Amazon Bedrock AgentCore Policy, see the [policy/README.md](policy/README.md) for detailed instructions.

This optional feature adds:
- **Patient Access Control**: Patients can only view and manage their own healthcare data
- **Provider Full Access**: Healthcare providers (doctors, nurses, admins) have unrestricted access
- **Cedar Policies**: Declarative security policies with runtime enforcement

**Quick Setup:**
```bash
# After setting up the gateway, add policy enforcement
python policy/setup_policy.py --gateway_id <gateway_id_here>

# Test policy enforcement
python policy/test_policy.py

# Run agent with policy enforcement (same command as before)
python strands_agent.py --gateway_id <gateway_id_here>
```


## Clean up instructions
### Optional: Remove Policy Enforcement

If you added policy enforcement, remove it first:

```bash
python policy/cleanup_policy.py --gateway_id <gateway_id_here>
```

### Clean up Infrastructure

Disable Cognito Auth with API Gateway

```
aws lambda invoke \
--function-name <input APIGWCognitoLambdaName as noted earlier> \
response.json \
--payload '{ "RequestType": "Delete" }' \
--cli-binary-format raw-in-base64-out \
--region <us-east-1 or us-west-2>
```

Delete the gateway and gateway target. If you created multiple gateways then repeat this step for all gateways.

```
python setup_fhir_mcp.py --op_type Delete --gateway_id <gateway_id_here>
```

Delete the cloudformation stack.

```
aws cloudformation delete-stack \
  --stack-name healthcare-cfn-stack \
  --region <us-east-1 or us-west-2>
```