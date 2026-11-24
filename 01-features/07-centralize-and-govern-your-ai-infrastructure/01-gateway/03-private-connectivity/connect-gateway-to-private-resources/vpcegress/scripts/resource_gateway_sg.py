"""Own a dedicated Resource Gateway SG; never open or select the VPC default SG."""
import argparse
import ipaddress
import json
import re
import time
import uuid

import boto3
from botocore.exceptions import ClientError

from lifecycle import STATE_DIR, account_for, begin, complete, load, record_path, reserve, save, verified_session

OWNER_TAG = 'agentcore-vpc-owner'


def owned_group(ec2, record):
    if not record.get('securityGroupId') or not record.get('ownerToken'):
        raise ValueError('Missing SG ownership response; reconcile the uncertain creation')
    group = ec2.describe_security_groups(GroupIds=[record['securityGroupId']])['SecurityGroups'][0]
    tags = {tag['Key']: tag['Value'] for tag in group.get('Tags', [])}
    if (group['GroupId'] != record['securityGroupId'] or group['VpcId'] != record['vpcId']
            or group.get('GroupName') == 'default' or tags.get(OWNER_TAG) != record['ownerToken']):
        raise RuntimeError('Security group ownership mismatch; refusing a shared/default/replaced group')
    return group


def reserve_group(*, name, vpc_id, destination_cidr, account_id, region, owner_token=None, parent_binding=None):
    """Exclusive local reservation; no AWS mutation and no same-name adoption."""
    destination = str(ipaddress.IPv4Network(destination_cidr, strict=True))
    path = record_path('resource-gateway-sg', name)
    record = {'name': name, 'vpcId': vpc_id, 'destinationCidr': destination,
              'accountId': account_id, 'region': region, 'ownerToken': owner_token or str(uuid.uuid4()), 'status': 'RESERVED'}
    if parent_binding is not None:
        record['parentBinding'] = dict(parent_binding)
    reserve(path, record)
    return record, path


def provision_group(ec2, record, path, *, on_created=None):
    """Only a fresh reservation can create; returned IDs are never overwritten."""
    if record.get('status') != 'RESERVED' or record.get('securityGroupId') or record.get('pendingOperation'):
        raise RuntimeError('Group creation already started; reconcile rather than recreate')
    record['status'] = 'CREATE_PENDING'
    begin(record, path, 'create_security_group')
    result = ec2.create_security_group(GroupName=f"agentcore-rg-{record['name']}",
        Description='Dedicated HTTPS egress for AgentCore Resource Gateway', VpcId=record['vpcId'],
        TagSpecifications=[{'ResourceType': 'security-group', 'Tags': [{'Key': OWNER_TAG, 'Value': record['ownerToken']}]}])
    complete(record, path, securityGroupId=result['GroupId'])
    if on_created is not None:
        on_created(record['securityGroupId'])
    group = owned_group(ec2, record)
    # EC2 gives NEW groups allow-all egress. Remove exactly their returned defaults
    # (IPv4/IPv6), then allow only HTTPS to the selected destination. No ingress.
    if group.get('IpPermissions'):
        raise RuntimeError('Unexpected ingress on the newly owned Resource Gateway group')
    if group.get('IpPermissionsEgress'):
        begin(record, path, 'revoke_default_egress')
        ec2.revoke_security_group_egress(GroupId=record['securityGroupId'], IpPermissions=group['IpPermissionsEgress'])
        complete(record, path)
    begin(record, path, 'authorize_https_egress')
    ec2.authorize_security_group_egress(GroupId=record['securityGroupId'], IpPermissions=[{
        'IpProtocol': 'tcp', 'FromPort': 443, 'ToPort': 443, 'IpRanges': [{'CidrIp': record['destinationCidr']}]}])
    complete(record, path, status='READY')
    return record


def create_group(ec2, *, name, vpc_id, destination_cidr, account_id, region):
    record, path = reserve_group(name=name, vpc_id=vpc_id, destination_cidr=destination_cidr,
                                 account_id=account_id, region=region)
    return provision_group(ec2, record, path)


def require_unused(record):
    if not record.get('securityGroupId') or not record.get('ownerToken'):
        raise ValueError('Missing SG ownership response; do not infer ownership by name')
    for target_path in STATE_DIR.glob('target-*.json'):
        target = load(target_path)
        groups = target.get('request', {}).get('privateEndpoint', {}).get('managedVpcResource', {}).get('securityGroupIds', [])
        if record['securityGroupId'] in groups and target.get('status') != 'DELETED':
            raise RuntimeError(f'Confirm target absence before SG cleanup: {target_path}')


def delete_group(ec2, record, path, *, parent_binding=None):
    if record.get('parentBinding') is not None and record['parentBinding'] != parent_binding:
        raise RuntimeError('Endpoint-owned group requires its verified parent binding for cleanup')
    require_unused(record)
    accepted = record.get('deleteAccepted', False)
    for attempt in range(30):
        try:
            owned_group(ec2, record)
        except ClientError as error:
            if error.response['Error']['Code'] != 'InvalidGroup.NotFound':
                raise
            complete(record, path, status='DELETED')
            return
        if not accepted:
            record['status'] = 'DELETING'
            save(path, record)
            try:
                ec2.delete_security_group(GroupId=record['securityGroupId'])
                accepted = True
                record['deleteAccepted'] = True
                save(path, record)
            except ClientError as error:
                if error.response['Error']['Code'] not in ('DependencyViolation', 'InvalidGroup.NotFound'):
                    raise
        if attempt < 29:
            time.sleep(10)
    raise TimeoutError(f'SG deletion/ENI release not confirmed; keep {path}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    parser.add_argument('--delete', action='store_true')
    parser.add_argument('--vpc-id')
    parser.add_argument('--destination-cidr', help='IPv4 destination: VPC CIDR for internal NLB, 0.0.0.0/0 for external HTTPS via NAT')
    args = parser.parse_args(argv)
    path = record_path('resource-gateway-sg', args.name)
    if args.delete:
        record = load(path)
        if record.get('parentBinding') is not None:
            raise ValueError('Use the owning endpoint cleanup for this bound child group')
        if record.get('status') == 'DELETED':
            print('Recorded group already deleted; not touching a reused name.')
            return
        delete_group(verified_session(record).client('ec2'), record, path)
    else:
        if not re.fullmatch(r'vpc-[0-9a-f]+', args.vpc_id or ''):
            parser.error('Provide --vpc-id')
        try:
            ipaddress.IPv4Network(args.destination_cidr or '', strict=True)
        except ValueError as error:
            parser.error(str(error))
        if path.exists():
            parser.error(f'Ownership record exists: {path}; reconcile/clean up first')
        session = boto3.Session()
        account = account_for(session)
        record = create_group(session.client('ec2'), name=args.name, vpc_id=args.vpc_id,
            destination_cidr=args.destination_cidr, account_id=account, region=session.region_name)
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
