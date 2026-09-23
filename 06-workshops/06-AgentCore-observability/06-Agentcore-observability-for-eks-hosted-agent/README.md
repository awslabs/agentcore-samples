# Deploying a Strands Agent to Amazon EKS

This example demonstrates how to deploy a Python application built with [Strands Agents SDK](https://github.com/strands-agents/sdk-python) to Amazon EKS. The example deploys a travel research agent application that runs as a containerized service in Amazon EKS with an Application Load Balancer.

The application is built with FastAPI and provides a `/travel` endpoint that returns travel information based on the provided prompt.

## Prerequisites

- [AWS CLI](https://aws.amazon.com/cli/) installed and configured
- [eksctl](https://eksctl.io/installation/) (v0.208.x or later) installed
- [Helm](https://helm.sh/) (v3 or later) installed
- [kubectl](https://docs.aws.amazon.com/eks/latest/userguide/install-kubectl.html) installed
- Either:
    - [Podman](https://podman.io/) installed and running
    - (or) [Docker](https://www.docker.com/) installed and running
- Amazon Bedrock Anthropic Claude model enabled in your AWS environment

## Quick Start (Automated Deployment)

For an automated deployment experience, use the included Jupyter notebook:

```bash
# Navigate to this directory
cd strands-travel-agent-eks

# Start Jupyter
jupyter notebook deploy.ipynb
```

The notebook automates the entire deployment process including:
- CloudWatch log group creation
- EKS cluster creation
- Docker image build and push to ECR
- IAM policy and Pod Identity configuration
- Helm chart deployment
- Port-forwarding and agent testing

> **Note:** The CloudWatch Observability addon (Section 8 in the notebook) is **optional**. It is NOT required for Bedrock AgentCore Observability. AgentCore sends telemetry directly to CloudWatch using the OTEL configuration in the Dockerfile.

**Environment Variables (Optional):**

Customize the deployment by setting these environment variables before running the notebook:

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region for deployment |
| `CLUSTER_NAME` | `eks-strands-agents-demo` | EKS cluster name |
| `SERVICE_NAME` | `strands-agents-travel` | Service name for Helm release |
| `LOG_GROUP_NAME` | `/strands-agents/travel` | CloudWatch log group |
| `LOG_STREAM_NAME` | `agent-logs` | CloudWatch log stream |
| `METRIC_NAMESPACE` | `StrandsAgents/Travel` | CloudWatch metrics namespace |
| `LOCAL_PORT` | `8080` | Local port for port-forwarding |

## Project Structure

```
.
├── README.md
├── deploy.ipynb              # Automated deployment notebook
├── chart/                    # Helm chart for Kubernetes deployment
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
└── docker/                   # Docker container files
    ├── Dockerfile
    ├── app/
    │   └── app.py           # FastAPI travel agent application
    └── requirements.txt
```

## Manual Deployment

The following sections describe the manual deployment steps. Use these if you prefer CLI commands over the automated notebook.

### Configuration

Before building the Docker image, update the following values in `docker/Dockerfile`:

| Variable | Description | Action Required |
|----------|-------------|-----------------|
| `OTEL_RESOURCE_ATTRIBUTES` | Service name for AgentCore Observability | Replace `<YOUR_SERVICE_NAME>` with your service name |
| `OTEL_EXPORTER_OTLP_LOGS_HEADERS` | OpenTelemetry observability configuration | Replace `<YOUR_LOG_GROUP>`, `<YOUR_LOG_STREAM>`, and `<YOUR_METRIC_NAMESPACE>` with your values |

The application also supports these runtime environment variables (defaults are set in `docker/app/app.py`):

| Variable | Description | Default |
|----------|-------------|---------|
| `MODEL_ID` | Amazon Bedrock model ID | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `MODEL_TEMPERATURE` | Model temperature for responses | `0` |
| `MODEL_MAX_TOKENS` | Maximum tokens in response | `1028` |

### Create EKS Auto Mode cluster

Set environment variables:
```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query 'Account' --output text)
export AWS_REGION=us-east-1
export CLUSTER_NAME=eks-strands-agents-demo
```

Create EKS Auto Mode cluster:
```bash
eksctl create cluster --name $CLUSTER_NAME --enable-auto-mode
```

Configure kubeconfig context:
```bash
aws eks update-kubeconfig --name $CLUSTER_NAME
```

### Building and Pushing Docker Image to ECR

Follow these steps to build the Docker image and push it to Amazon ECR:

1. Authenticate to Amazon ECR:
```bash
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com
```

2. Create the ECR repository if it doesn't exist:
```bash
aws ecr create-repository --repository-name strands-agents-travel --region ${AWS_REGION}
```

3. Build the Docker image:
```bash
docker build --platform linux/amd64 -t strands-agents-travel:latest docker/
```

4. Tag the image for ECR:
```bash
docker tag strands-agents-travel:latest ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/strands-agents-travel:latest
```

5. Push the image to ECR:
```bash
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/strands-agents-travel:latest
```

### Configure EKS Pod Identity to access Amazon Bedrock

Create an IAM policy to allow InvokeModel and InvokeModelWithResponseStream to all Amazon Bedrock models:
```bash
cat > bedrock-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam create-policy \
  --policy-name strands-agents-travel-bedrock-policy \
  --policy-document file://bedrock-policy.json
rm -f bedrock-policy.json
```

Create an EKS Pod Identity association:
```bash
eksctl create podidentityassociation --cluster $CLUSTER_NAME \
  --namespace default \
  --service-account-name strands-agents-travel \
  --permission-policy-arns arn:aws:iam::$AWS_ACCOUNT_ID:policy/strands-agents-travel-bedrock-policy \
  --role-name eks-strands-agents-travel
```

### Deploy strands-agents-travel application

Deploy the helm chart with the image from ECR:
```bash
helm install strands-agents-travel ./chart \
  --set image.repository=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/strands-agents-travel \
  --set image.tag=latest
```

Wait for Deployment to be available (Pods Running):
```bash
kubectl wait --for=condition=available deployments strands-agents-travel --all
```

### Test the Agent

Using kubernetes port-forward:
```bash
kubectl --namespace default port-forward service/strands-agents-travel 8080:80 &
```

Call the travel service:
```bash
curl -X POST \
  http://localhost:8080/travel \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "What are the best places to visit in Tokyo in March?"}'
```

### Expose Agent through Application Load Balancer

[Create an IngressClass to configure an Application Load Balancer](https://docs.aws.amazon.com/eks/latest/userguide/auto-configure-alb.html):
```bash
cat <<EOF | kubectl apply -f -
apiVersion: eks.amazonaws.com/v1
kind: IngressClassParams
metadata:
  name: alb
spec:
  scheme: internet-facing
EOF
```

```bash
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: IngressClass
metadata:
  name: alb
  annotations:
    ingressclass.kubernetes.io/is-default-class: "true"
spec:
  controller: eks.amazonaws.com/alb
  parameters:
    apiGroup: eks.amazonaws.com
    kind: IngressClassParams
    name: alb
EOF
```

Update helm deployment to create Ingress using the IngressClass created:
```bash
helm upgrade strands-agents-travel ./chart \
  --set image.repository=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/strands-agents-travel \
  --set image.tag=latest \
  --set ingress.enabled=true \
  --set ingress.className=alb
```

Get the ALB URL:
```bash
export ALB_URL=$(kubectl get ingress strands-agents-travel -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
echo "The shared ALB is available at: http://$ALB_URL"
```

Wait for ALB to be active:
```bash
aws elbv2 wait load-balancer-available --load-balancer-arns $(aws elbv2 describe-load-balancers --query 'LoadBalancers[?DNSName==`'"$ALB_URL"'`].LoadBalancerArn' --output text)
```

Call the travel service via Application Load Balancer:
```bash
curl -X POST \
  http://$ALB_URL/travel \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "What are the top attractions in Barcelona?"}'
```

### Configure High Availability and Resiliency

To configure high availability:
- Increase replicas to 3
- [Topology Spread Constraints](https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/): Spread workload across multi-az
- [Pod Disruption Budgets](https://kubernetes.io/docs/concepts/workloads/pods/disruptions/#pod-disruption-budgets): Tolerate minAvailable of 1

```bash
helm upgrade strands-agents-travel ./chart -f - <<EOF
image:
  repository: ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/strands-agents-travel
  tag: latest

ingress:
  enabled: true
  className: alb

replicaCount: 3

topologySpreadConstraints:
  - maxSkew: 1
    minDomains: 3
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        app.kubernetes.io/name: strands-agents-travel
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app.kubernetes.io/instance: strands-agents-travel

podDisruptionBudget:
  enabled: true
  minAvailable: 1
EOF
```

## Cleanup

Uninstall helm chart:
```bash
helm uninstall strands-agents-travel
```

Delete EKS Auto Mode cluster:
```bash
eksctl delete cluster --name $CLUSTER_NAME --wait
```

Delete IAM policy:
```bash
aws iam delete-policy --policy-arn arn:aws:iam::$AWS_ACCOUNT_ID:policy/strands-agents-travel-bedrock-policy
```

## License

This project is licensed under the Apache License 2.0 - see the LICENSE file for details.
