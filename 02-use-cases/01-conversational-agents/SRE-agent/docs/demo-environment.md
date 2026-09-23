# Demo Environment

The SRE Agent includes a demo environment that simulates infrastructure operations. This allows you to explore the system's capabilities without connecting to production systems.

**Important Note**: The data in [`backend/data`](../backend/data) is synthetically generated, and the backend directory contains stub servers that showcase how a real SRE agent backend could work. In a production environment, these implementations would need to be replaced with real implementations that connect to actual systems, use vector databases, and integrate with other data sources. This demo serves as an illustration of the architecture, where the backend components are designed to be plug-and-play replaceable.

## Starting the Demo Backend

> **🔒 SSL Requirement:** The backend servers must run with HTTPS when using AgentCore Gateway. Use the SSL commands from the Quick Start section.

The demo backend consists of four specialized API servers that provide realistic responses for different infrastructure domains:

```bash
# Start all demo servers with SSL (recommended)
cd backend

# Get your private IP for server binding
TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" -s)
PRIVATE_IP=$(curl -H "X-aws-ec2-metadata-token: $TOKEN" \
  -s http://169.254.169.254/latest/meta-data/local-ipv4)

# Start with SSL certificates
./scripts/start_demo_backend.sh \
  --host $PRIVATE_IP \
  --ssl-keyfile /opt/ssl/privkey.pem \
  --ssl-certfile /opt/ssl/fullchain.pem

# Alternative: Start without SSL (testing only - not compatible with AgentCore Gateway)
# ./scripts/start_demo_backend.sh --host 0.0.0.0

# The script starts four API servers:
# - Kubernetes API (port 8011): Simulates a K8s cluster with multiple namespaces
# - Logs API (port 8012): Provides searchable application logs with error injection
# - Metrics API (port 8013): Generates realistic performance metrics with anomalies
# - Runbooks API (port 8014): Serves operational procedures and troubleshooting guides
```

## Demo Scenarios

The demo environment includes several pre-configured scenarios that showcase the SRE Agent's capabilities:

**Database Pod Failure Scenario**: The demo includes failing database pods in the production namespace with associated error logs and resource exhaustion metrics. This scenario demonstrates how the agents collaborate to identify memory leaks as the root cause.

**API Gateway Latency Scenario**: Simulated high latency in the API gateway with corresponding slow query logs and CPU spikes. This showcases the system's ability to correlate issues across different data sources.

**Cascading Failure Scenario**: A complex scenario where a failing authentication service causes cascading failures across multiple services. This demonstrates the agent's ability to trace issues through distributed systems.

## Customizing Demo Data

The demo data is stored in JSON files under `backend/data/` and can be customized to match your specific use cases:

```bash
backend/data/
├── k8s_data/
│   ├── pods.json         # Pod definitions and status
│   ├── deployments.json  # Deployment configurations
│   └── events.json       # Cluster events
├── logs_data/
│   └── application_logs.json  # Log entries with various severity levels
├── metrics_data/
│   └── performance_metrics.json  # Time-series metrics data
└── runbooks_data/
    └── runbooks.json     # Operational procedures
```

## Stopping the Demo

```bash
# Stop all demo servers
cd backend
./scripts/stop_demo_backend.sh
```