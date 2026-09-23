"""
Mock data generator for Lab 2 testing
Provides realistic CloudWatch logs and metrics without requiring deployed infrastructure
"""

import datetime
import random

# EC2 Application Logs - Mix of normal operations and errors
EC2_APPLICATION_LOGS = [
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).timestamp() * 1000
        ),
        "message": "2024-11-03T14:55:00.123Z [INFO] Application started successfully",
        "logStreamName": "ec2-app-stream",
        "eventId": "1",
    },
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=4)).timestamp() * 1000
        ),
        "message": "2024-11-03T14:56:00.456Z [INFO] Database connection pool initialized. Size: 10",
        "logStreamName": "ec2-app-stream",
        "eventId": "2",
    },
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=3)).timestamp() * 1000
        ),
        "message": "2024-11-03T14:57:00.789Z [ERROR] Failed to connect to DynamoDB. Connection timeout after 30s",
        "logStreamName": "ec2-app-stream",
        "eventId": "3",
    },
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).timestamp() * 1000
        ),
        "message": "2024-11-03T14:58:00.234Z [WARN] Retrying DynamoDB connection. Attempt 2/5",
        "logStreamName": "ec2-app-stream",
        "eventId": "4",
    },
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).timestamp() * 1000
        ),
        "message": "2024-11-03T14:59:00.567Z [ERROR] Connection attempt 3 failed. Response time: 45000ms (threshold: 30000ms)",
        "logStreamName": "ec2-app-stream",
        "eventId": "5",
    },
    {
        "timestamp": int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000),
        "message": "2024-11-03T15:00:00.890Z [CRITICAL] Multiple connection failures detected. Circuit breaker activated.",
        "logStreamName": "ec2-app-stream",
        "eventId": "6",
    },
]

# NGINX Access/Error Logs - Mix of successful and error requests
NGINX_LOGS = [
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).timestamp() * 1000
        ),
        "message": '192.168.1.100 - - [03/Nov/2024:14:55:00 +0000] "GET /api/customers HTTP/1.1" 200 1245 "-" "Mozilla/5.0"',
        "logStreamName": "nginx-access",
        "eventId": "1",
    },
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=4)).timestamp() * 1000
        ),
        "message": '192.168.1.101 - - [03/Nov/2024:14:56:00 +0000] "POST /api/orders HTTP/1.1" 201 534 "-" "REST-Client"',
        "logStreamName": "nginx-access",
        "eventId": "2",
    },
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=3)).timestamp() * 1000
        ),
        "message": '192.168.1.102 - - [03/Nov/2024:14:57:00 +0000] "GET /api/customers HTTP/1.1" 502 162 "-" "Mozilla/5.0"',
        "logStreamName": "nginx-error",
        "eventId": "3",
    },
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).timestamp() * 1000
        ),
        "message": "2024/11/03 14:58:00 [error] 1234#0: *567 upstream timed out (110: Connection timed out) while connecting to upstream",
        "logStreamName": "nginx-error",
        "eventId": "4",
    },
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).timestamp() * 1000
        ),
        "message": '192.168.1.103 - - [03/Nov/2024:14:59:00 +0000] "GET /health HTTP/1.1" 503 0 "-" "HealthChecker"',
        "logStreamName": "nginx-access",
        "eventId": "5",
    },
    {
        "timestamp": int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000),
        "message": "2024/11/03 15:00:00 [alert] 1234#0: worker process 5678 exited on signal 11 (core dumped)",
        "logStreamName": "nginx-error",
        "eventId": "6",
    },
]

# DynamoDB Operation Logs - Mix of successful and throttled operations
DYNAMODB_LOGS = [
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).timestamp() * 1000
        ),
        "message": "2024-11-03T14:55:00.100Z [INFO] PutItem: table=Orders, latency=45ms, consumed_capacity=1",
        "logStreamName": "dynamodb-ops",
        "eventId": "1",
    },
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=4)).timestamp() * 1000
        ),
        "message": "2024-11-03T14:56:00.200Z [INFO] Query: table=Customers, latency=32ms, items_returned=15",
        "logStreamName": "dynamodb-ops",
        "eventId": "2",
    },
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=3)).timestamp() * 1000
        ),
        "message": "2024-11-03T14:57:00.300Z [WARN] ProvisionedThroughputExceededException: Orders table. Write capacity exceeded. Requested: 150, Available: 100",
        "logStreamName": "dynamodb-ops",
        "eventId": "3",
    },
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=2)).timestamp() * 1000
        ),
        "message": "2024-11-03T14:58:00.400Z [WARN] Batch write request throttled. Retry attempt 1/3 with exponential backoff",
        "logStreamName": "dynamodb-ops",
        "eventId": "4",
    },
    {
        "timestamp": int(
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).timestamp() * 1000
        ),
        "message": "2024-11-03T14:59:00.500Z [ERROR] Max retries exceeded for Orders table. Total backoff time: 5234ms",
        "logStreamName": "dynamodb-ops",
        "eventId": "5",
    },
    {
        "timestamp": int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000),
        "message": "2024-11-03T15:00:00.600Z [CRITICAL] Orders table unavailable. All operations failing with ServiceUnavailableException",
        "logStreamName": "dynamodb-ops",
        "eventId": "6",
    },
]


# CloudWatch Metrics - CPU, Memory, Disk with realistic values and spike
def get_cpu_metrics():
    """Generate realistic CPU utilization metrics"""
    now = datetime.datetime.now(datetime.timezone.utc)
    metrics = []

    # Normal values (60-70%)
    for i in range(3):
        metrics.append(
            {
                "Timestamp": now - datetime.timedelta(minutes=(5 - i * 2)),
                "Average": 65.0 + random.uniform(-5, 5),
                "Maximum": 72.0 + random.uniform(-3, 5),
                "Minimum": 58.0 + random.uniform(-3, 3),
                "Unit": "Percent",
            }
        )

    # Spike (95%+)
    metrics.append(
        {
            "Timestamp": now - datetime.timedelta(minutes=1),
            "Average": 94.5,
            "Maximum": 98.2,
            "Minimum": 89.1,
            "Unit": "Percent",
        }
    )

    # Recent spike continuation
    metrics.append(
        {
            "Timestamp": now,
            "Average": 96.8,
            "Maximum": 99.9,
            "Minimum": 91.2,
            "Unit": "Percent",
        }
    )

    return metrics


def get_memory_metrics():
    """Generate realistic memory utilization metrics"""
    now = datetime.datetime.now(datetime.timezone.utc)
    metrics = []

    # Normal values (70-80%)
    for i in range(3):
        metrics.append(
            {
                "Timestamp": now - datetime.timedelta(minutes=(5 - i * 2)),
                "Average": 75.0 + random.uniform(-3, 4),
                "Maximum": 82.0 + random.uniform(-2, 4),
                "Minimum": 68.0 + random.uniform(-2, 3),
                "Unit": "Percent",
            }
        )

    # Elevated (85%+)
    metrics.append(
        {
            "Timestamp": now - datetime.timedelta(minutes=1),
            "Average": 87.5,
            "Maximum": 92.1,
            "Minimum": 81.3,
            "Unit": "Percent",
        }
    )

    # High memory usage
    metrics.append(
        {
            "Timestamp": now,
            "Average": 89.2,
            "Maximum": 94.8,
            "Minimum": 83.5,
            "Unit": "Percent",
        }
    )

    return metrics


def get_disk_metrics():
    """Generate realistic disk utilization metrics"""
    now = datetime.datetime.now(datetime.timezone.utc)
    metrics = []

    # Normal values (45-55%)
    for i in range(5):
        metrics.append(
            {
                "Timestamp": now - datetime.timedelta(minutes=(5 - i)),
                "Average": 50.0 + random.uniform(-3, 3),
                "Maximum": 55.0 + random.uniform(-2, 3),
                "Minimum": 45.0 + random.uniform(-2, 2),
                "Unit": "Percent",
            }
        )

    return metrics


# Public API functions
def get_ec2_logs():
    """Return mock EC2 application logs"""
    return EC2_APPLICATION_LOGS


def get_nginx_logs():
    """Return mock NGINX logs"""
    return NGINX_LOGS


def get_dynamodb_logs():
    """Return mock DynamoDB operation logs"""
    return DYNAMODB_LOGS


def get_metrics(metric_name="CPUUtilization"):
    """Return mock metrics based on metric name"""
    if metric_name == "CPUUtilization":
        return get_cpu_metrics()
    elif metric_name == "MemoryUtilization":
        return get_memory_metrics()
    elif metric_name == "DiskUtilization":
        return get_disk_metrics()
    else:
        return []
