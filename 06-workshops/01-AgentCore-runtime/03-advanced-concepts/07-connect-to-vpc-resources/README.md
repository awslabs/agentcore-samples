# VPC Fargate Agent Runtime CDK Stack

This CDK stack deploys a Fargate container in a VPC with AWS Bedrock AgentCore Runtime. The container exposes two HTTP endpoints:

- `POST /invocations` - Main endpoint for processing requests
- `GET /ping` - Health check endpoint

## Architecture

The stack creates:

- **VPC** with public and private subnets across 2 availability zones
- **NAT Gateway** for private subnet internet access
- **Security Group** allowing inbound traffic on port 8080 within the VPC
- **Docker Image** built automatically by CDK and pushed to ECR (ARM64/Graviton)
- **AgentCore Runtime** running the container in private subnets
- **IAM Role** with necessary permissions for AgentCore operations

## Prerequisites

1. **AWS CLI** configured with appropriate credentials
2. **Node.js** and **npm** installed
3. **Docker** installed and running (required for building the container image)
4. **AWS CDK** bootstrapped in your account/region:
   ```bash
   npx cdk bootstrap
   ```

## Project Structure

```
07-connect-to-vpc-resources/
├── bin/
│   └── app.ts              # CDK app entry point
├── lib/
│   └── vpc-fargate-stack.ts # Main stack definition
├── agent-code/
│   ├── app.py              # Flask application with /ping and /invocations
│   ├── Dockerfile          # Container definition
│   └── requirements.txt    # Python dependencies
├── package.json            # NPM dependencies
├── tsconfig.json           # TypeScript configuration
└── cdk.json               # CDK configuration
```

## Installation

1. Install NPM dependencies:

   ```bash
   npm install
   ```

2. Build the TypeScript code:
   ```bash
   npm run build
   ```

## Deployment

### Deploy the Stack

```bash
npm run deploy
```

This command will:

1. Build the TypeScript CDK code
2. Build the Docker image for ARM64 (Graviton)
3. Push the image to ECR
4. Create the VPC, security groups, and networking
5. Deploy the AgentCore Runtime with the container

### View Synthesized CloudFormation Template

```bash
npm run synth
```

### Show Differences

To see what changes will be made before deploying:

```bash
npx cdk diff
```

## Stack Outputs

After deployment, the stack provides these outputs:

- **VpcId** - ID of the created VPC
- **SecurityGroupId** - ID of the security group
- **AgentRuntimeId** - ID of the AgentCore runtime
- **AgentRuntimeArn** - ARN of the AgentCore runtime
- **AgentRoleArn** - ARN of the execution role
- **DockerImageUri** - URI of the Docker image in ECR
- **ECRRepositoryName** - Name of the ECR repository

## Testing the Application

### Test Locally

You can test the Flask application locally before deploying:

```bash
cd agent-code
python app.py
```

Then in another terminal:

```bash
# Test the ping endpoint
curl http://localhost:8080/ping

# Test the invocations endpoint
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"test": "data"}'
```

### Test in AWS

After deployment, the container runs in private subnets and is not directly accessible from the internet. You would need to:

1. Set up a VPN or bastion host to access the private subnets
2. Use AWS PrivateLink or API Gateway to expose the service
3. Configure AgentCore to invoke the runtime

## Container Details

The container:

- Runs on **ARM64** architecture (Graviton) for better performance and cost
- Exposes port **8080**
- Runs as a non-root user (`bedrock_agentcore`)
- Includes a health check on the `/ping` endpoint
- Uses Python 3.11 with Flask

## Cleanup

To destroy all resources created by this stack:

```bash
npm run destroy
```

**Note:** This will delete the VPC, ECR repository (including all images), and all associated resources.

## Parameters

The stack accepts these parameters at deployment:

- **AgentName** (default: `VpcFargateAgent`) - Name for the agent runtime

To set parameters during deployment:

```bash
npx cdk deploy --parameters AgentName=MyCustomAgent
```

## Customization

### Modifying the Application

Edit `agent-code/app.py` to customize the application logic. The current implementation is a simple Flask app that:

- Returns health status on `GET /ping`
- Echoes received data on `POST /invocations`

### Modifying the Infrastructure

Edit `lib/vpc-fargate-stack.ts` to:

- Change VPC CIDR ranges
- Adjust number of availability zones
- Modify security group rules
- Add additional AWS resources

### Changing Python Dependencies

Edit `agent-code/requirements.txt` to add or update Python packages.

## Troubleshooting

### Docker Build Fails

Ensure Docker is running:

```bash
docker ps
```

### CDK Bootstrap Required

If you see an error about bootstrapping:

```bash
npx cdk bootstrap
```

### Permission Errors

Ensure your AWS credentials have sufficient permissions to:

- Create VPCs and networking resources
- Create ECR repositories and push images
- Create IAM roles and policies
- Create BedrockAgentCore resources

## Cost Considerations

This stack creates resources that incur costs:

- **NAT Gateway** - Hourly charges + data processing
- **ECR** - Storage costs for Docker images
- **Fargate** - vCPU and memory charges when runtime is active
- **VPC resources** - Data transfer charges

## Security

- Container runs in **private subnets** with no direct internet access
- Outbound internet access via NAT Gateway for pulling dependencies
- Security group restricts inbound traffic to port 8080 from within VPC only
- Container runs as non-root user
- IAM role follows principle of least privilege

## Additional Resources

- [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/)
- [AWS Fargate Documentation](https://docs.aws.amazon.com/fargate/)
