"""F1/F2/N1 regressions using real serializers/parsers, strict Stubber and no network."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import boto3
from botocore.exceptions import ClientError, WaiterError
from botocore.httpsession import URLLib3Session
from botocore.parsers import create_parser
from botocore.stub import Stubber
from botocore.validate import validate_parameters

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import create_credential, delete_credential, credential_generation, create_target
import configure_eks_endpoint, lifecycle, resource_gateway_sg

ACCOUNT = '123456789012'
REGION = 'us-west-2'
VPC = 'vpc-0123456789abcdef0'
SG = 'sg-0123456789abcdef0'
TOKEN = '00000000-0000-0000-0000-000000000001'
DAY = datetime(2026, 11, 18, tzinfo=timezone.utc)
OLD = f'arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:review-OLD123'
NEW = f'arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:review-NEW456'


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        real = boto3.session.Session(aws_access_key_id='offline', aws_secret_access_key='offline', region_name=REGION)
        self.clients = {n: real.client(n) for n in ['sts', 'bedrock-agentcore-control', 'ec2', 'elbv2', 'route53']}
        self.stubs = {n: Stubber(c) for n, c in self.clients.items()}
        for stub in self.stubs.values(): stub.activate()
        session = Mock(region_name=REGION)
        session.client.side_effect = self.clients.__getitem__
        self.patches = [patch.object(boto3, 'Session', return_value=session),
            patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK DENIED')),
            patch.object(URLLib3Session, 'send', side_effect=AssertionError('AWS NETWORK DENIED')),
            patch.object(lifecycle, 'STATE_DIR', self.root), patch.object(delete_credential, 'STATE_DIR', self.root),
            patch.object(resource_gateway_sg, 'STATE_DIR', self.root), patch.object(configure_eks_endpoint, 'STATE_DIR', self.root),
            patch.object(resource_gateway_sg.uuid, 'uuid4', return_value=TOKEN), patch('time.sleep')]
        for item in self.patches: item.start()

    def tearDown(self):
        try:
            for stub in self.stubs.values(): stub.assert_no_pending_responses()
        finally:
            for item in reversed(self.patches): item.stop()
            for stub in self.stubs.values(): stub.deactivate()
            self.temp.cleanup()

    def identity(self):
        self.stubs['sts'].add_response('get_caller_identity', {'Account': ACCOUNT, 'UserId': 'offline',
            'Arn': f'arn:aws:iam::{ACCOUNT}:user/offline'}, {})

    def response(self, method, payload, expected):
        client = self.clients['bedrock-agentcore-control']
        operation = client.meta.method_to_api_mapping[method]
        model = client.meta.service_model.operation_model(operation)
        # Exercise genuine wire response parsing, including modeled timestamps.
        body = json.dumps(payload, default=lambda value: value.timestamp()).encode()
        parsed = create_parser(client.meta.service_model.metadata['protocol']).parse(
            {'body': body, 'status_code': 200, 'headers': {'content-type': 'application/json'}}, model.output_shape)
        self.stubs['bedrock-agentcore-control'].add_response(method, parsed, expected)

    def provider_detail(self, kind, secret=OLD, day=DAY):
        segment = 'apikeycredentialprovider' if kind == 'api-key' else 'oauth2credentialprovider'
        result = {'name': 'review-'+kind, 'credentialProviderArn': f'arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:token-vault/default/{segment}/review-{kind}',
                  'createdTime': day, 'lastUpdatedTime': day}
        result['apiKeySecretArn' if kind == 'api-key' else 'clientSecretArn'] = {'secretArn': secret}
        if kind == 'oauth':
            result.update(credentialProviderVendor='CustomOauth2', oauth2ProviderConfigOutput={'customOauth2ProviderConfig': {
                'oauthDiscovery': {'discoveryUrl': 'https://idp.example.com/.well-known/openid-configuration'}}})
        return result

    def create_provider(self, kind):
        name = 'review-'+kind
        detail = self.provider_detail(kind)
        method = 'api_key' if kind == 'api-key' else 'oauth2'
        created = {k: v for k, v in detail.items() if k in ('name', 'credentialProviderArn', 'apiKeySecretArn', 'clientSecretArn', 'oauth2ProviderConfigOutput')}
        self.identity()
        if kind == 'api-key':
            params = {'name': name, 'apiKey': 'fake-value'}
            flags = ['--api-key-value', 'fake-value']
        else:
            params = {'name': name, 'credentialProviderVendor': 'CustomOauth2', 'oauth2ProviderConfigInput': {
                'customOauth2ProviderConfig': {'oauthDiscovery': {'discoveryUrl': 'https://idp.example.com/.well-known/openid-configuration'},
                                             'clientId': 'client', 'clientSecret': 'fake-value'}}}
            flags = ['--discovery-url', 'https://idp.example.com/.well-known/openid-configuration', '--client-id', 'client', '--client-secret', 'fake-value']
        self.response('create_'+method+'_credential_provider', created, params)
        self.response('get_'+method+'_credential_provider', detail, {'name': name})
        create_credential.main(['--name', name, '--type', kind, *flags])
        path = lifecycle.record_path('credential', name)
        record = lifecycle.load(path)
        self.assertEqual(record['secretArn'], OLD)
        self.assertEqual(record['createdTime'], DAY.isoformat())
        return path, record, method

    def delete_start(self, kind):
        self.identity()
        self.stubs['bedrock-agentcore-control'].add_response('list_gateways', {'items': []}, {'maxResults': 100})

    def test_both_provider_types_replacement_after_accepted_delete_is_not_deleted(self):
        for kind in ['api-key', 'oauth']:
            with self.subTest(kind=kind):
                path, record, method = self.create_provider(kind)
                name = record['name']; get = 'get_'+method+'_credential_provider'; delete = 'delete_'+method+'_credential_provider'
                self.delete_start(kind)
                self.response(get, self.provider_detail(kind), {'name': name})
                self.response(delete, {}, {'name': name})
                self.stubs['bedrock-agentcore-control'].add_client_error(get, 'AccessDeniedException', expected_params={'name': name})
                with self.assertRaises(ClientError): delete_credential.main(['--name', name, '--type', kind])
                self.assertEqual(lifecycle.load(path)['deleteIntent']['state'], 'ACCEPTED')
                self.delete_start(kind)
                self.response(get, self.provider_detail(kind, NEW, datetime(2026, 11, 19, tzinfo=timezone.utc)), {'name': name})
                # No DELETE response is queued: any replacement delete is a strict SDK failure.
                with self.assertRaisesRegex(RuntimeError, 'saved generation'):
                    delete_credential.main(['--name', name, '--type', kind])
                self.assertEqual(lifecycle.load(path)['secretArn'], OLD)
                self.assertEqual(lifecycle.load(path)['createdTime'], DAY.isoformat())

    def test_both_provider_types_same_secret_new_creation_marker_fails(self):
        for kind in ['api-key', 'oauth']:
            path, record, method = self.create_provider(kind)
            self.delete_start(kind)
            self.response('get_'+method+'_credential_provider', self.provider_detail(kind, OLD, datetime(2026, 11, 19, tzinfo=timezone.utc)), {'name': record['name']})
            with self.assertRaisesRegex(RuntimeError, 'createdTime'):
                delete_credential.main(['--name', record['name'], '--type', kind])
            self.assertEqual(lifecycle.load(path)['status'], 'CREATED')

    def test_both_provider_types_success_and_denial_preserve_identity(self):
        for kind in ['api-key', 'oauth']:
            path, record, method = self.create_provider(kind); name=record['name']
            get='get_'+method+'_credential_provider'; delete='delete_'+method+'_credential_provider'
            self.delete_start(kind); self.response(get, self.provider_detail(kind), {'name': name})
            self.stubs['bedrock-agentcore-control'].add_client_error(delete, 'AccessDeniedException', expected_params={'name': name})
            with self.assertRaises(ClientError): delete_credential.main(['--name', name, '--type', kind])
            self.assertEqual(lifecycle.load(path)['deleteIntent']['state'], 'REJECTED')
            self.delete_start(kind); self.response(get, self.provider_detail(kind), {'name': name}); self.response(delete, {}, {'name': name})
            self.stubs['bedrock-agentcore-control'].add_client_error(get, 'ResourceNotFoundException', expected_params={'name': name})
            delete_credential.main(['--name', name, '--type', kind])
            self.assertEqual(lifecycle.load(path)['status'], 'DELETED')

    def test_missing_generation_record_fails_before_clients(self):
        for kind in ['api-key', 'oauth']:
            path, record, _ = self.create_provider(kind)
            for field in ('secretArn', 'createdTime'):
                damaged = dict(record); del damaged[field]; lifecycle.save(path, damaged)
                with self.assertRaises(SystemExit): delete_credential.main(['--name', record['name'], '--type', kind])

    def group(self, destination='0.0.0.0/0'):
        return {'GroupId': SG, 'GroupName': 'agentcore-rg-test', 'VpcId': VPC, 'Tags': [{'Key': resource_gateway_sg.OWNER_TAG, 'Value': TOKEN}],
                'IpPermissions': [], 'IpPermissionsEgress': [{'IpProtocol': '-1', 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}]}

    def queue_group_create(self, name, destination):
        stub=self.stubs['ec2']
        stub.add_response('create_security_group', {'GroupId': SG}, {'GroupName': 'agentcore-rg-'+name,
            'Description': 'Dedicated HTTPS egress for AgentCore Resource Gateway', 'VpcId': VPC,
            'TagSpecifications': [{'ResourceType': 'security-group', 'Tags': [{'Key': resource_gateway_sg.OWNER_TAG, 'Value': TOKEN}]}]})
        stub.add_response('describe_security_groups', {'SecurityGroups': [self.group()]}, {'GroupIds': [SG]})
        stub.add_response('revoke_security_group_egress', {'Return': True}, {'GroupId': SG, 'IpPermissions': self.group()['IpPermissionsEgress']})
        stub.add_response('authorize_security_group_egress', {'Return': True}, {'GroupId': SG, 'IpPermissions': [{
            'IpProtocol': 'tcp', 'FromPort': 443, 'ToPort': 443, 'IpRanges': [{'CidrIp': destination}]}]})

    def target_request(self, name, group, subnet='subnet-0123456789abcdef0'):
        args=argparse.Namespace(vpc_id=VPC, subnet_ids=subnet, security_group_ids=group, routing_domain=None,
            resource_configuration_identifier=None, lambda_arn=None, endpoint='https://private.example.com/mcp',
            tool_schema_file=None, openapi_schema_file=None, listing_mode=None, credential_provider_arn=None,
            scopes=None, name=name, header_name='x-api-key')
        request=create_target.build_request(args, 'gateway-1234567890')
        validate_parameters(request, self.clients['bedrock-agentcore-control'].meta.service_model.operation_model('CreateGatewayTarget').input_shape)
        self.assertEqual(request['privateEndpoint']['managedVpcResource']['securityGroupIds'], [SG])
        return request

    def test_static_target_composes_owned_https_group_with_restricted_default_context(self):
        root=Path(__file__).resolve().parents[2]
        self.assertTrue(json.loads((root/'cdk.json').read_text())['context']['@aws-cdk/aws-ec2:restrictDefaultSecurityGroup'])
        if os.environ.get('VPC_NETWORK_EVIDENCE'):
            evidence=json.loads(Path(os.environ['VPC_NETWORK_EVIDENCE']).read_text())
            self.assertTrue(any(r['Type']=='Custom::VpcRestrictDefaultSG' for r in evidence.values()))
        self.queue_group_create('static-ip', '0.0.0.0/0')
        group=resource_gateway_sg.create_group(self.clients['ec2'], name='static-ip', vpc_id=VPC, destination_cidr='0.0.0.0/0', account_id=ACCOUNT, region=REGION)
        request=self.target_request('static-ip-target', group['securityGroupId'])
        self.assertEqual(len(request['privateEndpoint']['managedVpcResource']['subnetIds']), 1)
        doc=(root/'03-advanced-concepts/04-static-gateway-ip.md').read_text()
        self.assertIn('--security-group-ids "$RESOURCE_GATEWAY_SG_ID"', doc)
        self.assertIn('--subnet-ids "$STATIC_IP_SUBNET_ID"', doc)

    def test_group_cleanup_blocks_live_target_and_unowned_group(self):
        self.queue_group_create('test', '10.0.0.0/16')
        group=resource_gateway_sg.create_group(self.clients['ec2'], name='test', vpc_id=VPC, destination_cidr='10.0.0.0/16', account_id=ACCOUNT, region=REGION)
        path=lifecycle.record_path('resource-gateway-sg','test')
        target=lifecycle.record_path('target','test')
        lifecycle.save(target, {'status':'READY','request':self.target_request('test',SG)})
        with self.assertRaisesRegex(RuntimeError,'target absence'): resource_gateway_sg.delete_group(self.clients['ec2'],group,path)
        target.unlink()
        changed=self.group(); changed['Tags']=[]
        self.stubs['ec2'].add_response('describe_security_groups',{'SecurityGroups':[changed]},{'GroupIds':[SG]})
        with self.assertRaisesRegex(RuntimeError,'ownership mismatch'): resource_gateway_sg.delete_group(self.clients['ec2'],group,path)

    def test_group_cleanup_refuses_missing_owner_or_default_group(self):
        with self.assertRaises(ValueError):
            resource_gateway_sg.delete_group(self.clients['ec2'], {'securityGroupId': SG}, self.root/'sg.json')
        record={'securityGroupId':SG, 'ownerToken':TOKEN, 'vpcId':VPC}
        default=self.group(); default['GroupName']='default'
        self.stubs['ec2'].add_response('describe_security_groups', {'SecurityGroups':[default]}, {'GroupIds':[SG]})
        with self.assertRaisesRegex(RuntimeError,'ownership mismatch'):
            resource_gateway_sg.delete_group(self.clients['ec2'], record, self.root/'sg.json')

    def test_group_cleanup_denial_dependency_and_confirmed_absence(self):
        self.queue_group_create('test','10.0.0.0/16')
        group=resource_gateway_sg.create_group(self.clients['ec2'],name='test',vpc_id=VPC,destination_cidr='10.0.0.0/16',account_id=ACCOUNT,region=REGION)
        path=lifecycle.record_path('resource-gateway-sg','test');stub=self.stubs['ec2']
        stub.add_response('describe_security_groups',{'SecurityGroups':[self.group()]},{'GroupIds':[SG]})
        stub.add_client_error('delete_security_group','UnauthorizedOperation',expected_params={'GroupId':SG})
        with self.assertRaises(ClientError):resource_gateway_sg.delete_group(self.clients['ec2'],group,path)
        for error in ['DependencyViolation',None]:
            stub.add_response('describe_security_groups',{'SecurityGroups':[self.group()]},{'GroupIds':[SG]})
            if error:stub.add_client_error('delete_security_group',error,expected_params={'GroupId':SG})
            else:stub.add_response('delete_security_group',{'Return':True},{'GroupId':SG})
        stub.add_client_error('describe_security_groups','InvalidGroup.NotFound',expected_params={'GroupIds':[SG]})
        resource_gateway_sg.delete_group(self.clients['ec2'],group,path)
        self.assertEqual(lifecycle.load(path)['status'],'DELETED')

    def endpoint_setup(self, groups):
        args=argparse.Namespace(name='eks-test', nlb_arn=f'arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/net/review/123abc',
            vpc_id=VPC,zone_id='Z123ABC',domain='mcp.example.com',security_group_id=None)
        record={'name':args.name,'operationId':TOKEN,'accountId':ACCOUNT,'region':REGION,'status':'CREATE_PENDING'};path=self.root/'endpoint.json'
        self.stubs['elbv2'].add_response('describe_load_balancers',{'LoadBalancers':[{'LoadBalancerArn':args.nlb_arn,'VpcId':VPC,
            'Type':'network','Scheme':'internal','SecurityGroups':groups,'DNSName':'internal.example.com','CanonicalHostedZoneId':'Z234ABC'}]}, {'LoadBalancerArns':[args.nlb_arn]})
        dns=self.stubs['route53'];dns.add_response('get_hosted_zone',{'HostedZone':{'Id':args.zone_id,'Name':'example.com.','CallerReference':'review',
            'Config':{'PrivateZone':True}},'VPCs':[{'VPCId':VPC,'VPCRegion':REGION}]},{'Id':args.zone_id})
        rr={'Name':args.domain,'Type':'A','AliasTarget':{'DNSName':'internal.example.com','HostedZoneId':'Z234ABC','EvaluateTargetHealth':False}}
        change={'Id':'/change/CCREATE','Status':'INSYNC','SubmittedAt':DAY}
        dns.add_response('change_resource_record_sets',{'ChangeInfo':change},{'HostedZoneId':args.zone_id,'ChangeBatch':{'Changes':[{'Action':'CREATE','ResourceRecordSet':rr}]}})
        dns.add_response('get_change',{'ChangeInfo':change},{'Id':change['Id']})
        self.stubs['ec2'].add_response('describe_vpcs',{'Vpcs':[{'VpcId':VPC,'CidrBlock':'10.0.0.0/16'}]},{'VpcIds':[VPC]})
        return args,record,path,rr

    def test_no_sg_nlb_gets_dedicated_group_and_target_never_defaults(self):
        args,record,path,rr=self.endpoint_setup([])
        self.queue_group_create(args.name,'10.0.0.0/16')
        configure_eks_endpoint.apply(self.clients['ec2'],self.clients['elbv2'],self.clients['route53'],args,record,path)
        self.assertEqual(record['resourceGatewayGroupName'],args.name)
        self.target_request('eks-target',record['securityGroupId'])
        child=lifecycle.load(lifecycle.record_path('resource-gateway-sg',args.name))
        self.assertEqual(child['destinationCidr'],'10.0.0.0/16')
        self.assertEqual(record['ruleIds'],[])
        target_path=lifecycle.record_path('target','eks-target')
        # A target may reference the group using a different URL spelling. SG-ID
        # ownership must block cleanup before any DNS DELETE, not only afterward.
        target={'status':'READY','request':self.target_request('eks-target', SG)}
        lifecycle.save(target_path,target)
        with self.assertRaisesRegex(RuntimeError,'target absence'):
            configure_eks_endpoint.remove(self.clients['ec2'],self.clients['route53'],record,path)
        target['status']='DELETED';lifecycle.save(target_path,target)
        dns=self.stubs['route53'];query={'HostedZoneId':args.zone_id,'StartRecordName':args.domain,'StartRecordType':'A','MaxItems':'1'}
        dns.add_response('list_resource_record_sets',{'ResourceRecordSets':[rr],'IsTruncated':False,'MaxItems':'1'},query)
        change={'Id':'/change/CDELETE','Status':'INSYNC','SubmittedAt':DAY}
        dns.add_response('change_resource_record_sets',{'ChangeInfo':change},{'HostedZoneId':args.zone_id,'ChangeBatch':{'Changes':[{'Action':'DELETE','ResourceRecordSet':rr}]}})
        dns.add_response('get_change',{'ChangeInfo':change},{'Id':change['Id']})
        dns.add_response('list_resource_record_sets',{'ResourceRecordSets':[],'IsTruncated':False,'MaxItems':'1'},query)
        # Parent binding verification precedes DNS; group deletion independently rechecks ownership.
        self.stubs['ec2'].add_response('describe_security_groups',{'SecurityGroups':[self.group()]},{'GroupIds':[SG]})
        self.stubs['ec2'].add_response('describe_security_groups',{'SecurityGroups':[self.group()]},{'GroupIds':[SG]})
        self.stubs['ec2'].add_response('delete_security_group',{'Return':True},{'GroupId':SG})
        self.stubs['ec2'].add_client_error('describe_security_groups','InvalidGroup.NotFound',expected_params={'GroupIds':[SG]})
        configure_eks_endpoint.remove(self.clients['ec2'],self.clients['route53'],record,path)
        self.assertEqual(lifecycle.load(path)['status'],'DELETED')
        self.assertEqual(lifecycle.load(lifecycle.record_path('resource-gateway-sg', args.name))['status'],'DELETED')

    def test_dns_alias_reappearing_after_accepted_delete_is_not_deleted(self):
        rr={'Name':'mcp.example.com','Type':'A','AliasTarget':{'DNSName':'internal.example.com','HostedZoneId':'Z234ABC','EvaluateTargetHealth':False}}
        record={'zoneId':'Z123ABC','recordSet':rr,'aliasCreated':True,'dnsDeleteIntent':True,'dnsDeleteChangeId':'/change/CDELETE'}
        path=self.root/'alias.json';lifecycle.save(path,record)
        dns=self.stubs['route53'];change={'Id':'/change/CDELETE','Status':'INSYNC','SubmittedAt':DAY}
        dns.add_response('get_change',{'ChangeInfo':change},{'Id':change['Id']})
        dns.add_response('list_resource_record_sets',{'ResourceRecordSets':[rr],'IsTruncated':False,'MaxItems':'1'},
            {'HostedZoneId':'Z123ABC','StartRecordName':'mcp.example.com','StartRecordType':'A','MaxItems':'1'})
        with self.assertRaisesRegex(RuntimeError,'possible replacement'):
            configure_eks_endpoint.remove_alias(self.clients['route53'],record,path)
        self.assertTrue(lifecycle.load(path)['aliasCreated'])

    def test_dns_delete_waiter_failure_resumes_exact_change_without_redeleting(self):
        args,record,path,rr=self.endpoint_setup([SG]);dns=self.stubs['route53'];ec2=self.stubs['ec2']
        ec2.add_response('authorize_security_group_ingress',{'SecurityGroupRules':[{'SecurityGroupRuleId':'sgr-0123456789abcdef0'}]},
            {'GroupId':SG,'IpPermissions':[{'IpProtocol':'tcp','FromPort':443,'ToPort':443,'IpRanges':[{'CidrIp':'10.0.0.0/16'}]}]})
        configure_eks_endpoint.apply(self.clients['ec2'],self.clients['elbv2'],self.clients['route53'],args,record,path)
        query={'HostedZoneId':args.zone_id,'StartRecordName':args.domain,'StartRecordType':'A','MaxItems':'1'}
        dns.add_response('list_resource_record_sets',{'ResourceRecordSets':[rr],'IsTruncated':False,'MaxItems':'1'},query)
        change={'Id':'/change/CDELETE','Status':'PENDING','SubmittedAt':DAY}
        dns.add_response('change_resource_record_sets',{'ChangeInfo':change},{'HostedZoneId':args.zone_id,'ChangeBatch':{'Changes':[{'Action':'DELETE','ResourceRecordSet':rr}]}})
        dns.add_client_error('get_change','AccessDenied',expected_params={'Id':'/change/CDELETE'})
        with self.assertRaises(WaiterError):configure_eks_endpoint.remove(self.clients['ec2'],self.clients['route53'],record,path)
        record=lifecycle.load(path);self.assertEqual(record['dnsDeleteChangeId'],'/change/CDELETE')
        change['Status']='INSYNC';dns.add_response('get_change',{'ChangeInfo':change},{'Id':'/change/CDELETE'})
        dns.add_response('list_resource_record_sets',{'ResourceRecordSets':[],'IsTruncated':False,'MaxItems':'1'},query)
        ec2.add_response('revoke_security_group_ingress',{'Return':True},{'GroupId':SG,'SecurityGroupRuleIds':['sgr-0123456789abcdef0']})
        configure_eks_endpoint.remove(self.clients['ec2'],self.clients['route53'],record,path)
        self.assertEqual(lifecycle.load(path)['status'],'DELETED')
