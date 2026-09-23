# Backend Demo Infrastructure

This directory contains the complete demo backend infrastructure for SRE Agent testing and development.

## 📁 Structure

```
backend/
├── config_utils.py               # Configuration utilities
├── data/                         # Organized fake data
│   ├── k8s_data/                # Kubernetes mock data
│   ├── logs_data/               # Application logs
│   ├── metrics_data/            # Performance metrics
│   └── runbooks_data/           # Operational procedures
├── openapi_specs/               # API specifications
│   ├── k8s_api.yaml            # Kubernetes API spec
│   ├── logs_api.yaml           # Logs API spec
│   ├── metrics_api.yaml        # Metrics API spec
│   └── runbooks_api.yaml       # Runbooks API spec
├── servers/                     # Mock API implementations
│   ├── k8s_server.py           # Kubernetes API server
│   ├── logs_server.py          # Logs API server
│   ├── metrics_server.py       # Metrics API server
│   ├── runbooks_server.py      # Runbooks API server
│   ├── run_all_servers.py      # Start all servers
│   └── stop_servers.py         # Stop all servers
└── scripts/                    # Operational scripts
    ├── start_demo_backend.sh   # Simplified startup
    └── stop_demo_backend.sh    # Simplified shutdown
```

## 🚀 Quick Start

### Simple Startup (Recommended)
```bash
# Start all demo servers with simple Python HTTP servers
./scripts/start_demo_backend.sh
```

### Advanced Startup (Full FastAPI servers)
```bash
# Start full-featured servers with FastAPI
cd servers
python run_all_servers.py
```

## 🌐 API Endpoints

When running, the demo backend provides these endpoints:

- **Kubernetes API**: http://localhost:8001
- **Logs API**: http://localhost:8002  
- **Metrics API**: http://localhost:8003
- **Runbooks API**: http://localhost:8004

## 📊 Data Organization

### K8s Data (`data/k8s_data/`)
- `deployments.json` - Deployment status and configurations
- `pods.json` - Pod states and resource usage
- `events.json` - Cluster events and warnings

### Logs Data (`data/logs_data/`)
- `application_logs.json` - Application log entries
- `error_logs.json` - Error-specific log entries

### Metrics Data (`data/metrics_data/`)
- `performance_metrics.json` - Response times, throughput
- `resource_metrics.json` - CPU, memory, disk usage

### Runbooks Data (`data/runbooks_data/`)
- `incident_playbooks.json` - Incident response procedures
- `troubleshooting_guides.json` - Step-by-step guides

## 🔧 Server Implementations

### Simple HTTP Servers (Default)
Basic Python `http.server` implementations that serve JSON data directly from files.

### FastAPI Servers (Advanced)
Full-featured FastAPI servers with:
- OpenAPI documentation
- Request validation
- Response schemas
- Health endpoints

## 📋 OpenAPI Specifications

Complete OpenAPI 3.0 specifications for all APIs:
- Endpoint definitions
- Request/response schemas
- Authentication requirements
- Example data

## 🛑 Stopping Services

```bash
# Simple method
./scripts/stop_demo_backend.sh

# Advanced method  
cd servers
python stop_servers.py
```

## 🧪 Testing

Test individual APIs:
```bash
# Test K8s API
curl http://localhost:8001/health

# Test with specific endpoints
curl http://localhost:8001/api/v1/namespaces/production/pods
curl http://localhost:8002/api/v1/logs/search?query=error
```

## ⚙️ Configuration

The backend uses realistic data scenarios including:
- Failed database pods
- Memory pressure warnings
- Performance degradation patterns
- Common troubleshooting procedures

This provides a comprehensive testing environment for the SRE Agent system.