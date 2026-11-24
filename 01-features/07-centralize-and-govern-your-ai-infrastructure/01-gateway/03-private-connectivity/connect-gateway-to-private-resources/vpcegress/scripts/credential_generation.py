"""Generation evidence from actual AgentCore Identity response fields."""
from datetime import datetime, timezone


def secret_arn(response, kind):
    field = 'apiKeySecretArn' if kind == 'api-key' else 'clientSecretArn'
    value = response.get(field, {}).get('secretArn')
    if not isinstance(value, str) or not value:
        raise ValueError(f'Missing generation-specific {field}.secretArn')
    return value


def created_time(response):
    value = response.get('createdTime')
    if not isinstance(value, datetime):
        raise ValueError('Missing modeled createdTime; reconcile ownership, never infer it by name')
    return value.astimezone(timezone.utc).isoformat()


def verify_generation(record, response, *, require_time=True):
    if not record.get('secretArn') or secret_arn(response, record['type']) != record['secretArn']:
        raise RuntimeError('Provider secret ARN differs from the saved generation; refusing replacement/adoption')
    if response.get('credentialProviderArn') != record['arn']:
        raise RuntimeError('Provider ARN differs from the ownership record')
    if require_time and (not record.get('createdTime') or created_time(response) != record['createdTime']):
        raise RuntimeError('Provider createdTime differs from the saved generation; refusing replacement/adoption')
