# Helper tool functions for log and metrics retrieval

# AWS SDK and configuration
import boto3
import datetime

# Workshop configuration
from lab_helpers.config import AWS_REGION

# Initialize AWS clients
cloudwatch_client = boto3.client("logs", region_name=AWS_REGION)
ec2_client = boto3.client("ec2", region_name=AWS_REGION)
lambda_client = boto3.client("lambda", region_name=AWS_REGION)
sts_client = boto3.client("sts", region_name=AWS_REGION)


def fetch_crm_app_logs(log_group_name="/aws/sre-workshop/crm-application", hours=2, use_mock=False):
    """Fetch CRM application logs from CloudWatch"""
    if use_mock:
        return mock_data.get_ec2_logs()  # noqa: F821

    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        start_time = int((now - datetime.timedelta(hours=hours)).timestamp() * 1000)
        end_time = int(now.timestamp() * 1000)

        response = cloudwatch_client.filter_log_events(
            logGroupName=log_group_name,
            startTime=start_time,
            endTime=end_time,
            filterPattern="?error ?throttle",
            limit=500,
        )
        return response.get("events", [])
    except Exception as e:
        return [{"message": f"Error fetching EC2 logs: {str(e)}"}]


def fetch_ec2_logs(log_group_name="/aws/sre-workshop/application", hours=2, use_mock=False):
    """Fetch EC2 application logs from CloudWatch or mock data"""
    if use_mock:
        return mock_data.get_ec2_logs()  # noqa: F821

    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        start_time = int((now - datetime.timedelta(hours=hours)).timestamp() * 1000)
        end_time = int(now.timestamp() * 1000)

        response = cloudwatch_client.filter_log_events(
            logGroupName=log_group_name,
            startTime=start_time,
            endTime=end_time,
            filterPattern="?error ?throttle",
            limit=500,
        )
        return response.get("events", [])
    except Exception as e:
        return [{"message": f"Error fetching EC2 logs: {str(e)}"}]


def fetch_nginx_error_logs(log_group_name="/aws/sre-workshop/nginx/error", hours=2, use_mock=False):
    """Fetch NGINX error logs from CloudWatch or mock data"""
    if use_mock:
        return mock_data.get_nginx_logs()  # noqa: F821

    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        start_time = int((now - datetime.timedelta(hours=hours)).timestamp() * 1000)
        end_time = int(now.timestamp() * 1000)

        response = cloudwatch_client.filter_log_events(
            logGroupName=log_group_name,
            startTime=start_time,
            endTime=end_time,
            filterPattern="?error ?throttle",
            limit=500,
        )
        return response.get("events", [])
    except Exception as e:
        return [{"message": f"Error fetching NGINX error logs: {str(e)}"}]


def fetch_nginx_access_logs(log_group_name="/aws/sre-workshop/nginx/access", hours=24, use_mock=False):
    """Fetch NGINX access/eor logs from CloudWatch or mock data"""
    if use_mock:
        return mock_data.get_nginx_logs()  # noqa: F821

    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        start_time = int((now - datetime.timedelta(hours=hours)).timestamp() * 1000)
        end_time = int(now.timestamp() * 1000)

        response = cloudwatch_client.filter_log_events(
            logGroupName=log_group_name,
            startTime=start_time,
            endTime=end_time,
            limit=100,
        )
        return response.get("events", [])
    except Exception as e:
        return [{"message": f"Error fetching NGINX access logs: {str(e)}"}]


def fetch_dynamodb_metrics(table_name, period_minutes=60, use_mock=False):
    """Fetch DynamoDB operation logs from CloudWatch or mock data"""
    if use_mock:
        return mock_data.get_dynamodb_logs()  # noqa: F821

    try:
        end_time = datetime.datetime.utcnow()
        start_time = end_time - timedelta(minutes=period_minutes)  # noqa: F821

        # Query all metrics in one call using get_metric_data
        cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)
        response = cloudwatch.get_metric_data(
            MetricDataQueries=[
                {
                    "Id": "read_capacity",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/DynamoDB",
                            "MetricName": "ConsumedReadCapacityUnits",
                            "Dimensions": [{"Name": "TableName", "Value": table_name}],
                        },
                        "Period": 300,
                        "Stat": "Sum",
                    },
                },
                {
                    "Id": "write_capacity",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/DynamoDB",
                            "MetricName": "ConsumedWriteCapacityUnits",
                            "Dimensions": [{"Name": "TableName", "Value": table_name}],
                        },
                        "Period": 300,
                        "Stat": "Sum",
                    },
                },
                {
                    "Id": "throttled",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/DynamoDB",
                            "MetricName": "ThrottledRequests",
                            "Dimensions": [{"Name": "TableName", "Value": table_name}],
                        },
                        "Period": 300,
                        "Stat": "Sum",
                    },
                },
                {
                    "Id": "user_errors",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/DynamoDB",
                            "MetricName": "UserErrors",
                            "Dimensions": [{"Name": "TableName", "Value": table_name}],
                        },
                        "Period": 300,
                        "Stat": "Sum",
                    },
                },
                {
                    "Id": "system_errors",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/DynamoDB",
                            "MetricName": "SystemErrors",
                            "Dimensions": [{"Name": "TableName", "Value": table_name}],
                        },
                        "Period": 300,
                        "Stat": "Sum",
                    },
                },
                {
                    "Id": "latency",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/DynamoDB",
                            "MetricName": "SuccessfulRequestLatency",
                            "Dimensions": [{"Name": "TableName", "Value": table_name}],
                        },
                        "Period": 300,
                        "Stat": "Average",
                    },
                },
            ],
            StartTime=start_time,
            EndTime=end_time,
        )

        # Extract values from response
        result = {
            "table_name": table_name,
            "timestamp": end_time.isoformat(),
            "read_capacity": 0,
            "write_capacity": 0,
            "throttled_requests": 0,
            "user_errors": 0,
            "system_errors": 0,
            "avg_latency_ms": None,
        }

        for metric_result in response["MetricDataResults"]:
            metric_id = metric_result["Id"]
            values = metric_result["Values"]

            if values:
                if metric_id == "latency":
                    result["avg_latency_ms"] = sum(values) / len(values)
            else:
                result[metric_id.replace("_", "_")] = sum(values)

        return result
    except Exception as e:
        return [{"message": f"Error fetching DynamoDB logs: {str(e)}"}]


def get_cpu_metrics(instance_id, period_minutes=60):
    """Helper function to get a CloudWatch metric."""
    cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)
    end_time = datetime.datetime.utcnow()
    start_time = end_time - datetime.timedelta(minutes=period_minutes)

    # start_time = int((now - datetime.timedelta(hours=hours)).timestamp() * 1000)
    # end_time = int(now.timestamp() * 1000)

    response = cloudwatch.get_metric_data(
        MetricDataQueries=[
            {
                "Id": "m1",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/EC2",
                        "MetricName": "CPUUtilization",
                        "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                    },
                    "Period": 60,
                    "Stat": "Average",
                },
            }
        ],
        StartTime=start_time,
        EndTime=end_time,
    )

    values = response["MetricDataResults"][0]["Values"]
    return values[-1] if values else None


def get_memory_metrics(instance_id, period_minutes=60):
    """Helper function to get a CloudWatch metric."""
    cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)
    end_time = datetime.datetime.utcnow()
    start_time = end_time - datetime.timedelta(minutes=period_minutes)

    # start_time = int((now - datetime.timedelta(hours=hours)).timestamp() * 1000)
    # end_time = int(now.timestamp() * 1000)

    response = cloudwatch.get_metric_data(
        MetricDataQueries=[
            {
                "Id": "m1",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/EC2",
                        "MetricName": "mem_used_percent",
                        "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                    },
                    "Period": 60,
                    "Stat": "Average",
                },
            }
        ],
        StartTime=start_time,
        EndTime=end_time,
    )

    values = response["MetricDataResults"][0]["Values"]
    return values[-1] if values else None
