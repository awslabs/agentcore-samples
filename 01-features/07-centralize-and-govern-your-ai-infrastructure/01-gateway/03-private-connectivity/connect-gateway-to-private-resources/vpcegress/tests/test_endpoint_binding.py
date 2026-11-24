"""R2-B1: actual main entrypoints, modeled SDK replies and strict no-mutation queues."""

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Make this module runnable alone; do not rely on another test's import side effect.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import configure_eks_endpoint as endpoint
import lifecycle
import resource_gateway_sg as groups
import test_review_regressions as fixtures

A = fixtures.ACCOUNT
REGION = fixtures.REGION
VPC = fixtures.VPC
SG = fixtures.SG
TOKEN = fixtures.TOKEN
DAY = fixtures.DAY


class EndpointBindingTests(unittest.TestCase):
    # Reuse only fixture helpers, not inherited test methods.
    setUp = fixtures.ReviewRegressions.setUp
    tearDown = fixtures.ReviewRegressions.tearDown
    identity = fixtures.ReviewRegressions.identity
    response = fixtures.ReviewRegressions.response
    group = fixtures.ReviewRegressions.group
    queue_group_create = fixtures.ReviewRegressions.queue_group_create
    endpoint_setup = fixtures.ReviewRegressions.endpoint_setup

    def endpoint_main_setup(self, name="eks-test"):
        self.identity()
        args, _, _, rr = self.endpoint_setup([])
        args.name = name
        flags = [
            "--name",
            name,
            "--nlb-arn",
            args.nlb_arn,
            "--vpc-id",
            VPC,
            "--zone-id",
            args.zone_id,
            "--domain",
            args.domain,
        ]
        return flags, rr

    def cleanup_replies(self, rr, *, parent_id_missing=False):
        self.identity()
        # Exact SG identity/tag/VPC must be checked before any DNS cleanup.
        self.stubs["ec2"].add_response(
            "describe_security_groups",
            {"SecurityGroups": [self.group()]},
            {"GroupIds": [SG]},
        )
        query = {
            "HostedZoneId": "Z123ABC",
            "StartRecordName": "mcp.example.com",
            "StartRecordType": "A",
            "MaxItems": "1",
        }
        dns = self.stubs["route53"]
        change = {"Id": "/change/CDELETE", "Status": "INSYNC", "SubmittedAt": DAY}
        dns.add_response(
            "list_resource_record_sets",
            {"ResourceRecordSets": [rr], "IsTruncated": False, "MaxItems": "1"},
            query,
        )
        dns.add_response(
            "change_resource_record_sets",
            {"ChangeInfo": change},
            {
                "HostedZoneId": "Z123ABC",
                "ChangeBatch": {
                    "Changes": [{"Action": "DELETE", "ResourceRecordSet": rr}]
                },
            },
        )
        dns.add_response("get_change", {"ChangeInfo": change}, {"Id": change["Id"]})
        dns.add_response(
            "list_resource_record_sets",
            {"ResourceRecordSets": [], "IsTruncated": False, "MaxItems": "1"},
            query,
        )
        self.stubs["ec2"].add_response(
            "describe_security_groups",
            {"SecurityGroups": [self.group()]},
            {"GroupIds": [SG]},
        )
        self.stubs["ec2"].add_response(
            "delete_security_group", {"Return": True}, {"GroupId": SG}
        )
        self.stubs["ec2"].add_client_error(
            "describe_security_groups",
            "InvalidGroup.NotFound",
            expected_params={"GroupIds": [SG]},
        )

    def create_endpoint(self):
        flags, rr = self.endpoint_main_setup()
        self.queue_group_create("eks-test", "10.0.0.0/16")
        endpoint.main(flags)
        path = lifecycle.record_path("eks-endpoint", "eks-test")
        child_path = lifecycle.record_path("resource-gateway-sg", "eks-test")
        return path, child_path, rr

    def test_standalone_main_collision_is_not_parent_ownership(self):
        self.identity()
        self.queue_group_create("collision", "0.0.0.0/0")
        groups.main(
            ["--name", "collision", "--vpc-id", VPC, "--destination-cidr", "0.0.0.0/0"]
        )
        child_path = lifecycle.record_path("resource-gateway-sg", "collision")
        original = child_path.read_bytes()
        flags, _ = self.endpoint_main_setup("collision")
        with self.assertRaises(FileExistsError):
            endpoint.main(flags)
        parent_path = lifecycle.record_path("eks-endpoint", "collision")
        failed = parent_path.read_bytes()
        record = lifecycle.load(parent_path)
        self.assertIn("resourceGatewayGroupIntent", record)
        self.assertNotIn("resourceGatewayGroupBinding", record)
        self.assertNotIn("resourceGatewayGroupName", record)
        self.assertEqual(child_path.read_bytes(), original)
        self.identity()
        # No EC2/Route53 replies queued: even an ownership read there is unexpected.
        with self.assertRaisesRegex(RuntimeError, "Child binding mismatch"):
            endpoint.main(["--name", "collision", "--delete"])
        self.assertEqual(child_path.read_bytes(), original)
        self.assertEqual(parent_path.read_bytes(), failed)

    def test_partial_child_id_survives_parent_id_save_interruption(self):
        flags, rr = self.endpoint_main_setup()
        self.stubs["ec2"].add_response(
            "create_security_group",
            {"GroupId": SG},
            {
                "GroupName": "agentcore-rg-eks-test",
                "Description": "Dedicated HTTPS egress for AgentCore Resource Gateway",
                "VpcId": VPC,
                "TagSpecifications": [
                    {
                        "ResourceType": "security-group",
                        "Tags": [{"Key": groups.OWNER_TAG, "Value": TOKEN}],
                    }
                ],
            },
        )
        real_save = lifecycle.save

        def interrupted(path, record):
            if record.get("resourceGatewayGroupBinding", {}).get("securityGroupId"):
                raise OSError("simulated interruption before parent ID write")
            real_save(path, record)

        with (
            patch.object(endpoint, "save", side_effect=interrupted),
            self.assertRaises(OSError),
        ):
            endpoint.main(flags)
        parent_path = lifecycle.record_path("eks-endpoint", "eks-test")
        child_path = lifecycle.record_path("resource-gateway-sg", "eks-test")
        parent = lifecycle.load(parent_path)
        child = lifecycle.load(child_path)
        self.assertNotIn("securityGroupId", parent["resourceGatewayGroupBinding"])
        self.assertEqual(child["securityGroupId"], SG)
        self.assertEqual(child["parentBinding"]["operationId"], parent["operationId"])
        self.cleanup_replies(rr)
        endpoint.main(["--name", "eks-test", "--delete"])
        self.assertEqual(
            lifecycle.load(parent_path)["resourceGatewayGroupBinding"][
                "securityGroupId"
            ],
            SG,
        )
        self.assertEqual(lifecycle.load(child_path)["status"], "DELETED")

    def test_reserved_child_before_parent_link_or_api_keeps_recovery_evidence(self):
        flags, _ = self.endpoint_main_setup()
        real_save = lifecycle.save

        def interrupted(path, record):
            if record.get("resourceGatewayGroupBinding"):
                raise OSError(
                    "simulated interruption after exclusive child reservation"
                )
            real_save(path, record)

        with (
            patch.object(endpoint, "save", side_effect=interrupted),
            self.assertRaises(OSError),
        ):
            endpoint.main(flags)
        pp = lifecycle.record_path("eks-endpoint", "eks-test")
        cp = lifecycle.record_path("resource-gateway-sg", "eks-test")
        parent = lifecycle.load(pp)
        child = lifecycle.load(cp)
        self.assertNotIn("resourceGatewayGroupBinding", parent)
        self.assertEqual(child["status"], "RESERVED")
        self.assertEqual(child["parentBinding"]["operationId"], parent["operationId"])
        self.assertEqual(
            child["ownerToken"], parent["resourceGatewayGroupIntent"]["ownerToken"]
        )
        before = (pp.read_bytes(), cp.read_bytes())
        self.identity()
        with self.assertRaisesRegex(ValueError, "no recorded EC2 ID"):
            endpoint.main(["--name", "eks-test", "--delete"])
        self.assertEqual((pp.read_bytes(), cp.read_bytes()), before)

    def test_intent_before_child_reservation_interruption_never_adopts(self):
        flags, _ = self.endpoint_main_setup()
        with (
            patch.object(
                groups, "reserve_group", side_effect=OSError("before reservation")
            ),
            self.assertRaises(OSError),
        ):
            endpoint.main(flags)
        pp = lifecycle.record_path("eks-endpoint", "eks-test")
        before = pp.read_bytes()
        self.assertFalse(
            lifecycle.record_path("resource-gateway-sg", "eks-test").exists()
        )
        self.identity()
        with self.assertRaises(FileNotFoundError):
            endpoint.main(["--name", "eks-test", "--delete"])
        self.assertEqual(pp.read_bytes(), before)

    def test_lost_create_response_keeps_bound_intent_but_refuses_id_guess(self):
        flags, _ = self.endpoint_main_setup()
        self.stubs["ec2"].add_client_error(
            "create_security_group",
            "InternalError",
            http_status_code=500,
            expected_params={
                "GroupName": "agentcore-rg-eks-test",
                "Description": "Dedicated HTTPS egress for AgentCore Resource Gateway",
                "VpcId": VPC,
                "TagSpecifications": [
                    {
                        "ResourceType": "security-group",
                        "Tags": [{"Key": groups.OWNER_TAG, "Value": TOKEN}],
                    }
                ],
            },
        )
        from botocore.exceptions import ClientError

        with self.assertRaises(ClientError):
            endpoint.main(flags)
        pp = lifecycle.record_path("eks-endpoint", "eks-test")
        cp = lifecycle.record_path("resource-gateway-sg", "eks-test")
        before = (pp.read_bytes(), cp.read_bytes())
        self.identity()
        with self.assertRaisesRegex(ValueError, "no recorded EC2 ID"):
            endpoint.main(["--name", "eks-test", "--delete"])
        self.assertEqual((pp.read_bytes(), cp.read_bytes()), before)

    def test_stale_missing_or_mismatched_binding_prevents_all_cleanup(self):
        pp, cp, _ = self.create_endpoint()
        parent = lifecycle.load(pp)
        child = lifecycle.load(cp)
        cases = []
        for field in [
            "ownerToken",
            "vpcId",
            "accountId",
            "region",
            "securityGroupId",
            "destinationCidr",
        ]:
            changed = copy.deepcopy(child)
            changed[field] = "different"
            cases.append((parent, changed))
        changed = copy.deepcopy(child)
        del changed["parentBinding"]
        cases.append((parent, changed))
        changed = copy.deepcopy(child)
        changed["parentBinding"]["operationId"] = "old-generation"
        cases.append((parent, changed))
        changed = copy.deepcopy(parent)
        del changed["resourceGatewayGroupIntent"]
        cases.append((changed, child))
        changed = copy.deepcopy(parent)
        changed["operationId"] = "other-generation"
        cases.append((changed, child))
        changed = copy.deepcopy(parent)
        changed["resourceGatewayGroupBinding"]["securityGroupId"] = "sg-other"
        cases.append((changed, child))
        for p, c in cases:
            with self.subTest(parent=p, child=c):
                lifecycle.save(pp, p)
                lifecycle.save(cp, c)
                before = (pp.read_bytes(), cp.read_bytes())
                with self.assertRaises((ValueError, RuntimeError)):
                    endpoint.remove(
                        self.clients["ec2"],
                        self.clients["route53"],
                        lifecycle.load(pp),
                        pp,
                    )
                self.assertEqual((pp.read_bytes(), cp.read_bytes()), before)

    def test_bound_ids_and_generation_cannot_be_recreated_or_overwritten(self):
        pp, cp, _ = self.create_endpoint()
        parent = lifecycle.load(pp)
        child = lifecycle.load(cp)
        before = (pp.read_bytes(), cp.read_bytes())
        with self.assertRaisesRegex(RuntimeError, "already started"):
            endpoint.apply(
                self.clients["ec2"],
                self.clients["elbv2"],
                self.clients["route53"],
                None,
                parent,
                pp,
            )
        with self.assertRaisesRegex(RuntimeError, "immutable"):
            endpoint.bind_group_id(parent, pp, "sg-11111111111111111")
        with self.assertRaisesRegex(RuntimeError, "already started"):
            groups.provision_group(self.clients["ec2"], child, cp)
        self.assertEqual((pp.read_bytes(), cp.read_bytes()), before)

    def test_parent_binding_blocks_standalone_child_delete(self):
        _pp, cp, _ = self.create_endpoint()
        with self.assertRaisesRegex(ValueError, "owning endpoint"):
            groups.main(["--name", "eks-test", "--delete"])
        with self.assertRaisesRegex(RuntimeError, "verified parent binding"):
            groups.delete_group(self.clients["ec2"], lifecycle.load(cp), cp)

    def test_completed_parent_cleanup_does_not_follow_reused_child_name(self):
        pp, cp, rr = self.create_endpoint()
        self.cleanup_replies(rr)
        endpoint.main(["--name", "eks-test", "--delete"])
        self.assertEqual(lifecycle.load(pp)["status"], "DELETED")
        cp.rename(
            self.root / "archived-child.json"
        )  # Preserve completed record; no record edits.
        new_id = "sg-11111111111111111"
        new_token = "00000000-0000-0000-0000-000000000099"
        self.identity()
        new_group = self.group()
        new_group.update(
            GroupId=new_id, Tags=[{"Key": groups.OWNER_TAG, "Value": new_token}]
        )
        self.stubs["ec2"].add_response(
            "create_security_group",
            {"GroupId": new_id},
            {
                "GroupName": "agentcore-rg-eks-test",
                "Description": "Dedicated HTTPS egress for AgentCore Resource Gateway",
                "VpcId": VPC,
                "TagSpecifications": [
                    {
                        "ResourceType": "security-group",
                        "Tags": [{"Key": groups.OWNER_TAG, "Value": new_token}],
                    }
                ],
            },
        )
        self.stubs["ec2"].add_response(
            "describe_security_groups",
            {"SecurityGroups": [new_group]},
            {"GroupIds": [new_id]},
        )
        self.stubs["ec2"].add_response(
            "revoke_security_group_egress",
            {"Return": True},
            {"GroupId": new_id, "IpPermissions": new_group["IpPermissionsEgress"]},
        )
        self.stubs["ec2"].add_response(
            "authorize_security_group_egress",
            {"Return": True},
            {
                "GroupId": new_id,
                "IpPermissions": [
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 443,
                        "ToPort": 443,
                        "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                    }
                ],
            },
        )
        with patch.object(groups.uuid, "uuid4", return_value=new_token):
            groups.main(
                [
                    "--name",
                    "eks-test",
                    "--vpc-id",
                    VPC,
                    "--destination-cidr",
                    "0.0.0.0/0",
                ]
            )
        before = cp.read_bytes()
        endpoint.main(["--name", "eks-test", "--delete"])  # No SDK calls queued.
        endpoint.remove(
            self.clients["ec2"], self.clients["route53"], lifecycle.load(pp), pp
        )
        self.assertEqual(cp.read_bytes(), before)
