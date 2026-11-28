"""SDK-model validation and partial-failure tests; no provisioning or sockets."""

import argparse
import json
import os
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.update(
    AWS_ACCESS_KEY_ID="testing",
    AWS_SECRET_ACCESS_KEY="testing",
    AWS_EC2_METADATA_DISABLED="true",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import botocore.session
import configure_eks_endpoint
import configure_gateway_permissions
import lattice_resources
import lifecycle
import proxy_backend
import static_ip
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters


class ModelClient:
    """Validate real request shapes but return deterministic, clearly fake resources."""

    def __init__(self, service, responses):
        self.model = botocore.session.get_session().get_service_model(service)
        self.responses = responses
        self.calls = []

    def __getattr__(self, name):
        if name == "get_waiter":
            return lambda name: Mock()
        operation = "".join(word.title() for word in name.split("_"))

        def call(**kwargs):
            validate_parameters(
                kwargs, self.model.operation_model(operation).input_shape
            )
            self.calls.append((name, kwargs))
            response = self.responses[name]
            if isinstance(response, Exception):
                raise response
            return response

        return call


def denied(operation="test"):
    return ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "offline denial"}},
        operation,
    )


class InfrastructureContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.guard = patch.object(
            socket.socket, "connect", side_effect=AssertionError("NETWORK DENIED")
        )
        self.guard.start()
        self.account_guard = patch.object(
            lattice_resources, "account_for", return_value="123456789012"
        )
        self.account_guard.start()

    def tearDown(self):
        self.account_guard.stop()
        self.guard.stop()
        self.temp.cleanup()

    def test_static_ip_reuses_route_and_single_eip(self):
        ec2 = ModelClient(
            "ec2",
            {
                "describe_route_tables": {
                    "RouteTables": [
                        {
                            "Routes": [
                                {
                                    "DestinationCidrBlock": "0.0.0.0/0",
                                    "NatGatewayId": "nat-123",
                                    "State": "active",
                                }
                            ]
                        }
                    ]
                },
                "describe_nat_gateways": {
                    "NatGateways": [
                        {
                            "State": "available",
                            "NatGatewayAddresses": [{"PublicIp": "192.0.2.3"}],
                        }
                    ]
                },
            },
        )
        self.assertEqual(
            static_ip.identify(ec2, "subnet-aaa")["elasticIp"], "192.0.2.3"
        )
        self.assertTrue(all(name.startswith("describe_") for name, _ in ec2.calls))
        ec2.responses["describe_route_tables"] = {"RouteTables": []}
        with self.assertRaises(ValueError):
            static_ip.identify(ec2, "subnet-aaa")

    def test_lattice_sdk_lifecycle(self):
        arn = "arn:aws:vpc-lattice:us-west-2:123456789012:resourceconfiguration/rcfg-0123456789abcdef0"
        client = ModelClient(
            "vpc-lattice",
            {
                "create_resource_gateway": {"id": "rgw-0123456789abcdef0"},
                "get_resource_gateway": {"status": "ACTIVE"},
                "create_resource_configuration": {"arn": arn},
                "get_resource_configuration": {"status": "ACTIVE"},
            },
        )
        session = Mock(region_name="us-west-2")
        session.client.return_value = client
        with (
            patch.object(lifecycle, "STATE_DIR", self.root),
            patch.object(lattice_resources.boto3, "Session", return_value=session),
        ):
            lattice_resources.main(
                [
                    "--name",
                    "test",
                    "--vpc-id",
                    "vpc-123",
                    "--subnet-ids",
                    "subnet-aaa,subnet-bbb",
                    "--security-group-ids",
                    "sg-123",
                    "--dns-name",
                    "private.example.com",
                ]
            )
        record = json.loads((self.root / "lattice-test.json").read_text())
        self.assertEqual(record["resourceConfigurationArn"], arn)
        create = next(
            kwargs
            for name, kwargs in client.calls
            if name == "create_resource_configuration"
        )
        self.assertEqual(create["portRanges"], ["443"])
        self.assertEqual(create["resourceGatewayIdentifier"], "rgw-0123456789abcdef0")

    def test_lattice_failed_config_retains_gateway(self):
        client = ModelClient(
            "vpc-lattice",
            {
                "create_resource_gateway": {"id": "rgw-0123456789abcdef0"},
                "get_resource_gateway": {"status": "ACTIVE"},
                "create_resource_configuration": denied(),
            },
        )
        session = Mock(region_name="us-west-2")
        session.client.return_value = client
        with (
            patch.object(lifecycle, "STATE_DIR", self.root),
            patch.object(lattice_resources.boto3, "Session", return_value=session),
            self.assertRaises(ClientError),
        ):
            lattice_resources.main(
                [
                    "--name",
                    "test",
                    "--vpc-id",
                    "vpc-123",
                    "--subnet-ids",
                    "subnet-aaa",
                    "--security-group-ids",
                    "sg-123",
                    "--dns-name",
                    "private.example.com",
                ]
            )
        self.assertEqual(
            json.loads((self.root / "lattice-test.json").read_text())[
                "resourceGatewayId"
            ],
            "rgw-0123456789abcdef0",
        )

    def test_lattice_delete_denial_not_absence(self):
        client = ModelClient("vpc-lattice", {"delete_resource_configuration": denied()})
        with self.assertRaises(ClientError):
            lattice_resources.delete_resource(
                client, "resource_configuration", "rcfg-0123456789abcdef0"
            )

    def test_proxy_preserves_https_host_rewrite_and_internal_scheme(self):
        ec2 = ModelClient(
            "ec2",
            {
                "describe_vpcs": {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]},
                "create_security_group": {"GroupId": "sg-123"},
                "authorize_security_group_ingress": {},
            },
        )
        elb = ModelClient(
            "elbv2",
            {
                "create_load_balancer": {
                    "LoadBalancers": [
                        {
                            "LoadBalancerArn": "alb-arn",
                            "DNSName": "internal.example.com",
                        }
                    ]
                },
                "create_target_group": {"TargetGroups": [{"TargetGroupArn": "tg-arn"}]},
                "register_targets": {},
                "create_listener": {"Listeners": [{"ListenerArn": "listener-arn"}]},
                "create_rule": {"Rules": [{"RuleArn": "rule-arn"}]},
            },
        )
        args = argparse.Namespace(
            name="test-proxy",
            vpc_id="vpc-123",
            subnet_ids="subnet-aaa,subnet-bbb",
            backend_ip="10.0.1.4",
            backend_domain="backend.example.com",
            certificate_arn="arn:aws:acm:us-west-2:123456789012:certificate/00000000-0000-0000-0000-000000000000",
        )
        record, path = {}, self.root / "proxy.json"
        proxy_backend.create_owned(ec2, elb, record, path, args)
        calls = dict(elb.calls)
        self.assertEqual(calls["create_load_balancer"]["Scheme"], "internal")
        self.assertEqual(calls["create_target_group"]["Protocol"], "HTTPS")
        self.assertEqual(
            calls["create_rule"]["Transforms"][0]["HostHeaderRewriteConfig"][
                "Rewrites"
            ][0]["Replace"],
            "backend.example.com",
        )
        self.assertEqual(json.loads(path.read_text())["status"], "CREATED")

    def test_proxy_delete_denial_preserves_record(self):
        path = self.root / "proxy.json"
        record = {"loadBalancerArn": "alb-arn", "securityGroupId": "sg-123"}
        lifecycle.save(path, record)
        elb = ModelClient("elbv2", {"delete_load_balancer": denied()})
        with (
            patch.object(proxy_backend, "STATE_DIR", self.root),
            self.assertRaises(ClientError),
        ):
            proxy_backend.delete_owned(Mock(), elb, record, path)
        self.assertEqual(json.loads(path.read_text()), record)

    def test_uncertain_create_cannot_report_deleted(self):
        for record in [
            {"status": "CREATE_PENDING"},
            {"pendingOperation": "create_dns_alias", "securityGroupId": "sg-123"},
        ]:
            with self.subTest(record=record), self.assertRaises(RuntimeError):
                lifecycle.require_reconciled(record)
        client = Mock()
        with self.assertRaises(RuntimeError):
            proxy_backend.delete_owned(
                client, client, {"status": "CREATE_PENDING"}, self.root / "proxy.json"
            )
        client.assert_not_called()

    def test_eks_alias_create_and_only_owned_ingress(self):
        arn = "arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/net/lab/123abc"
        elb = ModelClient(
            "elbv2",
            {
                "describe_load_balancers": {
                    "LoadBalancers": [
                        {
                            "VpcId": "vpc-123",
                            "Type": "network",
                            "Scheme": "internal",
                            "SecurityGroups": ["sg-123"],
                            "DNSName": "internal.example.com",
                            "CanonicalHostedZoneId": "Z123ABC",
                        }
                    ]
                }
            },
        )
        dns = ModelClient(
            "route53",
            {
                "get_hosted_zone": {
                    "HostedZone": {"Config": {"PrivateZone": True}},
                    "VPCs": [{"VPCId": "vpc-123"}],
                },
                "change_resource_record_sets": {"ChangeInfo": {"Id": "/change/test"}},
            },
        )
        ec2 = ModelClient(
            "ec2",
            {
                "describe_vpcs": {"Vpcs": [{"CidrBlock": "10.0.0.0/16"}]},
                "authorize_security_group_ingress": {
                    "SecurityGroupRules": [{"SecurityGroupRuleId": "sgr-owned"}]
                },
            },
        )
        args = argparse.Namespace(
            nlb_arn=arn,
            vpc_id="vpc-123",
            zone_id="Z123ABC",
            domain="private.example.com",
        )
        record, path = {}, self.root / "eks.json"
        configure_eks_endpoint.apply(ec2, elb, dns, args, record, path)
        self.assertEqual(record["ruleIds"], ["sgr-owned"])
        self.assertNotIn("pendingOperation", record)
        change = dict(dns.calls)["change_resource_record_sets"]
        self.assertEqual(change["ChangeBatch"]["Changes"][0]["Action"], "CREATE")
        self.assertTrue(record["aliasCreated"])

    def test_eks_optional_security_group_modes(self):
        choose = configure_eks_endpoint.select_security_group
        self.assertIsNone(choose({}))
        self.assertEqual(choose({"SecurityGroups": ["sg-one"]}), "sg-one")
        self.assertEqual(
            choose({"SecurityGroups": ["sg-front", "sg-back"]}, "sg-front"), "sg-front"
        )
        with self.assertRaises(ValueError):
            choose({"SecurityGroups": ["sg-front", "sg-back"]})
        with self.assertRaises(ValueError):
            choose({"SecurityGroups": ["sg-front"]}, "sg-unowned")

    def test_eks_uncertain_alias_response_blocks_cleanup(self):
        client = Mock()
        record = {"status": "CREATE_PENDING", "pendingOperation": "create_dns_alias"}
        with self.assertRaises(RuntimeError):
            configure_eks_endpoint.remove(
                client, client, record, self.root / "eks.json"
            )
        client.assert_not_called()

    def test_certificate_delete_denial_is_not_swallowed(self):
        parent = Path(__file__).resolve().parents[2]
        code = re.search(
            r"code: lambda.Code.fromInline\(`(.*?)`\)",
            (parent / "lib/private-cert-backend-stack.ts").read_text(),
            re.DOTALL,
        )[1]
        namespace = {}
        exec(compile(code, "inline-certificate-handler", "exec"), namespace)  # noqa: S102 -- checked-in Lambda source; handler uses mocked SDK
        acm = Mock()
        acm.delete_certificate.side_effect = denied()
        event = {
            "RequestType": "Delete",
            "PhysicalResourceId": "arn:aws:acm:us-west-2:123456789012:certificate/test",
            "ResourceProperties": {},
        }
        with (
            patch.object(namespace["boto3"], "client", return_value=acm),
            self.assertRaises(ClientError),
        ):
            namespace["handler"](event, None)

    def test_sg_guard_never_deletes_or_sends_cfnresponse(self):
        parent = Path(__file__).resolve().parents[2]
        code = re.search(
            r"code: lambda.Code.fromInline\(`(.*?)`\)",
            (parent / "lib/vpcegress-stack.ts").read_text(),
            re.DOTALL,
        )[1]
        namespace = {}
        exec(compile(code, "inline-sg-guard", "exec"), namespace)  # noqa: S102 -- checked-in Lambda source; handler uses mocked SDK
        ec2 = Mock()
        ec2.get_paginator.return_value.paginate.return_value = [
            {"SecurityGroups": [{"GroupId": "sg-unowned", "GroupName": "external"}]}
        ]
        event = {"RequestType": "Delete", "ResourceProperties": {"VpcId": "vpc-123"}}
        with (
            patch.object(namespace["boto3"], "client", return_value=ec2),
            self.assertRaises(RuntimeError),
        ):
            namespace["handler"](event, None)
        ec2.delete_security_group.assert_not_called()
        self.assertNotIn("cfnresponse", namespace)

    def test_permissions_writer_local_only(self):
        lifecycle.save(
            self.root / "credential-test.json",
            {
                "status": "CREATED",
                "type": "api-key",
                "arn": "provider-arn",
                "secretArn": "secret-arn",
            },
        )
        with (
            patch.object(lifecycle, "STATE_DIR", self.root),
            patch.object(configure_gateway_permissions, "STATE_DIR", self.root),
        ):
            configure_gateway_permissions.main(["--credential", "test"])
        result = json.loads((self.root / "gateway-permissions.json").read_text())
        self.assertEqual(
            result["providers"],
            [
                {
                    "type": "api-key",
                    "providerArn": "provider-arn",
                    "secretArn": "secret-arn",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
