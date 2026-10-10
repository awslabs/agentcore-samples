"""An in-memory AWS for devbox.py's flow tests: no credentials, no network.

Every call is checked against the botocore service model first (required members, types, and the
max length, pattern and enum rules botocore itself doesn't check), then recorded in World.log, then
answered from in-memory state. A call the fake doesn't implement fails the test, so an unexpected
operation (say, UpdateCapacityProvider) can't slip through.
"""

from __future__ import annotations

import base64
import copy
import datetime
import itertools
import json
import re
import uuid

import botocore.session
from botocore import xform_name
from botocore.exceptions import ClientError
from botocore.validate import ParamValidator

ACCOUNT = "111122223333"
REGION = "us-east-1"
_SESSION = botocore.session.get_session()
_MODELS: dict = {}


def model(service: str):
    if service not in _MODELS:
        _MODELS[service] = _SESSION.get_service_model(service)
    return _MODELS[service]


def strict_errors(shape, value, path: str = "") -> list[str]:
    """max, pattern and enum checks (botocore validates only required, type and min on the client)."""
    out: list[str] = []
    md = shape.metadata
    t = shape.type_name
    if t == "structure" and isinstance(value, dict):
        for k, v in value.items():
            if k in shape.members:
                out += strict_errors(shape.members[k], v, f"{path}.{k}")
    elif t == "list" and isinstance(value, list):
        if "max" in md and len(value) > md["max"]:
            out.append(f"{path}: {len(value)} items > max {md['max']}")
        for i, v in enumerate(value):
            out += strict_errors(shape.member, v, f"{path}[{i}]")
    elif t == "map" and isinstance(value, dict):
        if "max" in md and len(value) > md["max"]:
            out.append(f"{path}: {len(value)} entries > max {md['max']}")
        for k, v in value.items():
            out += strict_errors(shape.key, k, f"{path}<key {k}>")
            out += strict_errors(shape.value, v, f"{path}[{k}]")
    elif t == "string" and isinstance(value, str):
        if "max" in md and len(value) > md["max"]:
            out.append(f"{path}: length {len(value)} > max {md['max']}")
        if getattr(shape, "enum", None) and value not in shape.enum:
            out.append(f"{path}: {value!r} not in {shape.enum}")
        pat = md.get("pattern")
        if pat:
            try:
                rx = re.compile(pat)
            except re.error:
                rx = None  # Java-only syntax such as \p{L}
            if rx and not (rx.fullmatch(value) or (pat.startswith("^") or pat.endswith("$")) and rx.search(value)):
                out.append(f"{path}: {value!r} doesn't match {pat}")
    elif t in ("integer", "long") and isinstance(value, int) and "max" in md and value > md["max"]:
        out.append(f"{path}: {value} > max {md['max']}")
    return out


def name_field(tags: list[dict]) -> dict:
    """{"Name": <the Name tag>} as EFS describes a resource, or {} without one."""
    names = [t["Value"] for t in tags if t["Key"] == "Name"]
    return {"Name": names[-1]} if names else {}


def validate(service: str, op: str, params: dict) -> None:
    shape = model(service).operation_model(op).input_shape
    errors = []
    if shape is not None:
        report = ParamValidator().validate(params, shape)
        if report.has_errors():
            errors.append(report.generate_report())
        errors += strict_errors(shape, params)
    elif params:
        errors.append("takes no parameters")
    if errors:
        raise AssertionError(f"{service}.{op}: " + "; ".join(errors))


def error(code: str, op: str = "Op", msg: str = "", status: int = 400) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": msg or code}, "ResponseMetadata": {"HTTPStatusCode": status}}, op
    )


class FakeWaiter:
    def __init__(self, world, service, name):
        self.world, self.service, self.name = world, service, name

    def wait(self, **kw):
        self.world.log.append((self.service, f"waiter:{self.name}", kw))
        if self.name == "nat_gateway_available":
            for nid in kw.get("NatGatewayIds", []):
                if self.world.nats[nid]["State"] == "pending":
                    self.world.nats[nid]["State"] = "available"


class FakeClient:
    def __init__(self, world, service: str, impl):
        self._world, self._service, self._impl = world, service, impl
        self._ops = {xform_name(o): o for o in model(service).operation_names}

    def get_waiter(self, name):
        return FakeWaiter(self._world, self._service, name)

    def __getattr__(self, name):
        if name.startswith("_") or name not in self._ops:
            raise AttributeError(name)
        op = self._ops[name]

        def call(**params):
            validate(self._service, op, params)
            self._world.log.append((self._service, op, copy.deepcopy(params)))
            fn = getattr(self._impl, name, None)
            if fn is None:
                raise AssertionError(f"the fake {self._service} doesn't implement {op} (an unexpected call?)")
            return fn(**params)

        return call


def tags_of(spec) -> list[dict]:
    return [t for s in spec or [] for t in s.get("Tags", [])]


def match_filters(tags: list[dict], attrs: dict, filters: list[dict] | None) -> bool:
    tag = {t["Key"]: t["Value"] for t in tags or []}
    for f in filters or []:
        name, values = f["Name"], f["Values"]
        if name.startswith("tag:"):
            if tag.get(name[4:]) not in values:
                return False
        elif name == "tag-key":
            if not any(k in values for k in tag):
                return False
        elif attrs.get(name) not in values:
            return False
    return True


class World:
    """All the state, plus the log of every call as (service, operation, params)."""

    def __init__(self):
        self.log: list[tuple] = []
        self._n = itertools.count(1)
        self.account = ACCOUNT
        # ec2
        self.vpcs, self.vpc_attrs, self.subnets, self.igws, self.sgs, self.sg_rules = {}, {}, {}, {}, {}, {}
        self.addresses, self.nats, self.rts = {}, {}, {}
        # iam, ecr
        self.roles, self.profiles, self.repos = {}, {}, {}
        # agentcore
        self.cps, self.runtimes, self.gateways, self.targets, self.engines, self.policies, self.rbps = (
            {},
            {},
            {},
            {},
            {},
            {},
            {},
        )
        self.deleted_sessions: list[tuple[str, str]] = []
        self.cp_delete_failures = 0
        # logs (query_names: SNI names in the firewall's alert log; query_http_names: plain-HTTP Hosts; dns_query_names: DNS
        # Firewall alerts/blocks in the Resolver query log), network firewall, DNS Firewall
        self.log_groups, self.query_names, self.query_http_names, self.dns_query_names = {}, [], [], []
        self.dns_lists, self.dns_groups, self.dns_rules, self.dns_assocs, self.dns_fail_open = {}, {}, {}, {}, {}
        self.qlcs, self.qlc_assocs, self.qlc_assoc_outcome = (
            {},
            {},
            "ACTIVE",
        )  # ACTION_NEEDED: can't write the log group
        self.rule_groups, self.fw_policies, self.firewall, self.fw_logging = {}, {}, None, []
        # lambda, cloudfront
        self.fn, self.fn_url, self.fn_statements = None, None, []
        self.oacs, self.dists = {}, {}
        # efs (file systems, mount targets and their security groups, access points, file system policies), VPC endpoints
        self.efs_fs, self.efs_mts, self.efs_mt_sgs, self.efs_aps, self.efs_policies = {}, {}, {}, {}, {}
        self.endpoints = {}
        self.enis = {}  # only AgentCore's leftovers matter here (InterfaceType agentic_ai): undeploy leaves what they hold
        self.az_ids = {
            "us-east-1a": "use1-az1",
            "us-east-1b": "use1-az2",
            "us-east-1c": "use1-az4",
            "us-east-1d": "use1-az6",
            "us-east-1e": "use1-az3",
        }
        # the provisioner: its Lambda (the edge one is self.fn), the box table, the HTTP API, managed policies
        self.fns, self.fn_policies, self.fn_concurrency = {}, {}, {}
        self.tables, self.apis, self.managed_policies = {}, {}, {}
        # identity
        self.idc_groups = {"devbox-users", "ai-claude-power", "ai-claude-standard"}
        self.permission_sets = ["ClaudeCode-Power", "ClaudeCode-Standard"]
        self.idc_users = {
            "ada.lovelace@example.com": "00uADA0000000000000A",
            "grace.hopper@example.com": "00uGRACE00000000000G",
        }

    def hex(self, n: int = 17) -> str:
        return format(next(self._n), f"0{n}x")

    def suffix(self) -> str:
        return format(next(self._n), "010d").replace("0", "a")[:10]

    def token(self) -> str:
        return str(uuid.uuid4())

    def calls(self, service: str | None = None, op: str | None = None) -> list[tuple]:
        return [c for c in self.log if (service is None or c[0] == service) and (op is None or c[1] == op)]

    def ops(self) -> list[str]:
        return [c[1] for c in self.log]

    def clients(self) -> dict:
        return {
            "sts": FakeClient(self, "sts", Sts(self)),
            "org:sts": FakeClient(self, "sts", Sts(self)),
            "org:sso-admin": FakeClient(self, "sso-admin", SsoAdmin(self)),
            "org:identitystore": FakeClient(self, "identitystore", IdentityStore(self)),
            "ec2": FakeClient(self, "ec2", Ec2(self)),
            "iam": FakeClient(self, "iam", Iam(self)),
            "ecr": FakeClient(self, "ecr", Ecr(self)),
            "logs": FakeClient(self, "logs", Logs(self)),
            "network-firewall": FakeClient(self, "network-firewall", Nfw(self)),
            "lambda": FakeClient(self, "lambda", Lambda(self)),
            "cloudfront": FakeClient(self, "cloudfront", CloudFront(self)),
            "bedrock-agentcore-control": FakeClient(self, "bedrock-agentcore-control", AgentCoreControl(self)),
            "bedrock-agentcore": FakeClient(self, "bedrock-agentcore", AgentCoreData(self)),
            "route53resolver": FakeClient(self, "route53resolver", Resolver(self)),
            "efs": FakeClient(self, "efs", Efs(self)),
            "dynamodb": FakeClient(self, "dynamodb", DynamoDb(self)),
            "apigatewayv2": FakeClient(self, "apigatewayv2", ApiGatewayV2(self)),
        }


class Svc:
    def __init__(self, w: World):
        self.w = w


class Sts(Svc):
    def get_caller_identity(self):
        return {
            "Account": ACCOUNT,
            "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/Admin/tester",
            "UserId": "AROAEXAMPLE:tester",
        }


class SsoAdmin(Svc):
    ARN = "arn:aws:sso:::instance/ssoins-1234567890abcdef"

    def list_instances(self, **kw):
        return {"Instances": [{"InstanceArn": self.ARN, "IdentityStoreId": "d-1234567890"}]}

    def list_permission_sets(self, InstanceArn, **kw):
        return {
            "PermissionSets": [
                f"arn:aws:sso:::permissionSet/ssoins-1234567890abcdef/ps-{i:016x}"
                for i in range(len(self.w.permission_sets))
            ]
        }

    def describe_permission_set(self, InstanceArn, PermissionSetArn):
        return {
            "PermissionSet": {
                "Name": self.w.permission_sets[int(PermissionSetArn.rsplit("-", 1)[-1], 16)],
                "PermissionSetArn": PermissionSetArn,
            }
        }


class IdentityStore(Svc):
    def get_user_id(self, IdentityStoreId, AlternateIdentifier):
        email = AlternateIdentifier["UniqueAttribute"]["AttributeValue"]
        if email not in self.w.idc_users:
            raise error("ResourceNotFoundException", "GetUserId")
        return {
            "UserId": str(uuid.uuid5(uuid.NAMESPACE_DNS, email)),
            "IdentityStoreId": IdentityStoreId,
            "_email": email,
        }

    def get_group_id(self, IdentityStoreId, AlternateIdentifier):
        name = AlternateIdentifier["UniqueAttribute"]["AttributeValue"]
        if name not in self.w.idc_groups:
            raise error("ResourceNotFoundException", "GetGroupId")
        return {"GroupId": str(uuid.uuid5(uuid.NAMESPACE_DNS, "group:" + name)), "IdentityStoreId": IdentityStoreId}

    def describe_user(self, IdentityStoreId, UserId):
        email = next(e for e in self.w.idc_users if str(uuid.uuid5(uuid.NAMESPACE_DNS, e)) == UserId)
        okta = self.w.idc_users[email]
        ext = [{"Issuer": "https://scim.aws.com/1234", "Id": okta}] if okta else []
        return {
            "UserId": UserId,
            "IdentityStoreId": IdentityStoreId,
            "UserName": email,
            **({"ExternalIds": ext} if ext else {}),
        }


class Ec2(Svc):
    def describe_availability_zones(self, ZoneNames=None, **kw):
        return {
            "AvailabilityZones": [
                {"ZoneName": z, "ZoneId": self.w.az_ids[z], "RegionName": REGION, "State": "available"}
                for z in ZoneNames or self.w.az_ids
                if z in self.w.az_ids
            ]
        }

    # VPC endpoints (the S3 gateway endpoint adds a prefix-list route to each of its route tables)
    PREFIX_LIST = "pl-63a5400a"

    def create_vpc_endpoint(
        self, VpcId, VpcEndpointType, ServiceName, RouteTableIds, PolicyDocument, TagSpecifications
    ):
        assert VpcEndpointType == "Gateway" and VpcId in self.w.vpcs
        assert all(self.w.rts[r]["VpcId"] == VpcId for r in RouteTableIds)
        json.loads(PolicyDocument)
        eid = f"vpce-{self.w.hex()}"
        self.w.endpoints[eid] = {
            "VpcEndpointId": eid,
            "VpcEndpointType": VpcEndpointType,
            "VpcId": VpcId,
            "ServiceName": ServiceName,
            "State": "available",
            "PolicyDocument": PolicyDocument,
            "RouteTableIds": list(RouteTableIds),
            "Tags": tags_of(TagSpecifications),
        }
        for r in RouteTableIds:
            self._endpoint_route(r, eid, True)
        return {"VpcEndpoint": copy.deepcopy(self.w.endpoints[eid])}

    def _endpoint_route(self, rt_id, eid, add):
        rt = self.w.rts[rt_id]
        rt["Routes"] = [r for r in rt["Routes"] if r.get("GatewayId") != eid]
        if add:
            rt["Routes"].append({"DestinationPrefixListId": self.PREFIX_LIST, "GatewayId": eid, "State": "active"})

    def describe_vpc_endpoints(self, Filters=None, VpcEndpointIds=None, **kw):
        out = []
        for e in self.w.endpoints.values():
            attrs = {"vpc-id": e["VpcId"], "service-name": e["ServiceName"], "vpc-endpoint-type": e["VpcEndpointType"]}
            if match_filters(e["Tags"], attrs, Filters) and (
                VpcEndpointIds is None or e["VpcEndpointId"] in VpcEndpointIds
            ):
                out.append(copy.deepcopy(e))
        return {"VpcEndpoints": out}

    def modify_vpc_endpoint(self, VpcEndpointId, PolicyDocument=None, AddRouteTableIds=None, RemoveRouteTableIds=None):
        e = self.w.endpoints[VpcEndpointId]
        if PolicyDocument is not None:
            e["PolicyDocument"] = PolicyDocument
        for r in AddRouteTableIds or []:
            e["RouteTableIds"].append(r)
            self._endpoint_route(r, VpcEndpointId, True)
        for r in RemoveRouteTableIds or []:
            e["RouteTableIds"].remove(r)
            self._endpoint_route(r, VpcEndpointId, False)
        return {"Return": True}

    def delete_vpc_endpoints(self, VpcEndpointIds):
        for eid in VpcEndpointIds:
            for r in self.w.endpoints[eid]["RouteTableIds"]:
                self._endpoint_route(r, eid, False)
            del self.w.endpoints[eid]
        return {"Unsuccessful": []}

    # VPC
    def create_vpc(self, CidrBlock, TagSpecifications):
        vid = f"vpc-{self.w.hex()}"
        self.w.vpcs[vid] = {
            "VpcId": vid,
            "CidrBlock": CidrBlock,
            "State": "available",
            "Tags": tags_of(TagSpecifications),
        }
        self.w.vpc_attrs[vid] = {"EnableDnsSupport": True, "EnableDnsHostnames": False}
        rid = f"rtb-{self.w.hex()}"  # the main route table every VPC gets
        self.w.rts[rid] = {
            "RouteTableId": rid,
            "VpcId": vid,
            "Tags": [],
            "Routes": [{"DestinationCidrBlock": CidrBlock, "GatewayId": "local", "State": "active"}],
            "Associations": [
                {"Main": True, "RouteTableAssociationId": f"rtbassoc-{self.w.hex()}", "RouteTableId": rid}
            ],
        }
        return {"Vpc": copy.deepcopy(self.w.vpcs[vid])}

    def describe_vpcs(self, Filters=None, VpcIds=None):
        return {
            "Vpcs": [
                copy.deepcopy(v)
                for v in self.w.vpcs.values()
                if match_filters(v["Tags"], {"vpc-id": v["VpcId"]}, Filters)
            ]
        }

    def describe_vpc_attribute(self, VpcId, Attribute):
        key = Attribute[0].upper() + Attribute[1:]
        return {"VpcId": VpcId, key: {"Value": self.w.vpc_attrs[VpcId][key]}}

    def modify_vpc_attribute(self, VpcId, **kw):
        for k, v in kw.items():
            self.w.vpc_attrs[VpcId][k] = v["Value"]
        return {}

    def create_tags(self, Resources, Tags):
        for r in Resources:
            res = self.w.vpcs[r]
            res["Tags"] = [t for t in res["Tags"] if t["Key"] not in {x["Key"] for x in Tags}] + Tags
        return {}

    def delete_tags(self, Resources, Tags):
        for r in Resources:
            self.w.vpcs[r]["Tags"] = [t for t in self.w.vpcs[r]["Tags"] if t["Key"] not in {x["Key"] for x in Tags}]
        return {}

    def delete_vpc(self, VpcId):
        assert not [e for e in self.w.endpoints.values() if e["VpcId"] == VpcId], "a VPC endpoint is still in the VPC"
        assert not [g for g in self.w.sgs.values() if g["VpcId"] == VpcId and g["GroupName"] != "default"], (
            "security groups left"
        )
        assert not [a for a in self.w.dns_assocs.values() if a["VpcId"] == VpcId], (
            "a DNS Firewall rule group is still on the VPC"
        )
        assert not [a for a in self.w.qlc_assocs.values() if a["ResourceId"] == VpcId], (
            "query logging is still on the VPC"
        )
        assert not [s for s in self.w.subnets.values() if s["VpcId"] == VpcId], "subnets left"
        assert not [g for g in self.w.igws.values() if any(a["VpcId"] == VpcId for a in g["Attachments"])], (
            "internet gateway attached"
        )
        del self.w.vpcs[VpcId]
        self.w.rts = {k: v for k, v in self.w.rts.items() if v["VpcId"] != VpcId}
        return {}

    # subnets
    def create_subnet(self, VpcId, CidrBlock, AvailabilityZone, TagSpecifications):
        sid = f"subnet-{self.w.hex()}"
        self.w.subnets[sid] = {
            "SubnetId": sid,
            "VpcId": VpcId,
            "CidrBlock": CidrBlock,
            "AvailabilityZone": AvailabilityZone,
            "MapPublicIpOnLaunch": False,
            "Tags": tags_of(TagSpecifications),
        }
        return {"Subnet": copy.deepcopy(self.w.subnets[sid])}

    def describe_subnets(self, Filters=None):
        return {
            "Subnets": [
                copy.deepcopy(s)
                for s in self.w.subnets.values()
                if match_filters(s["Tags"], {"vpc-id": s["VpcId"]}, Filters)
            ]
        }

    def delete_subnet(self, SubnetId):
        if [m for m in self.w.efs_mts.values() if m["SubnetId"] == SubnetId]:
            raise error("DependencyViolation", "DeleteSubnet", "the subnet has dependencies (an EFS mount target)")
        del self.w.subnets[SubnetId]
        return {}

    # internet gateway
    def create_internet_gateway(self, TagSpecifications):
        gid = f"igw-{self.w.hex()}"
        self.w.igws[gid] = {"InternetGatewayId": gid, "Attachments": [], "Tags": tags_of(TagSpecifications)}
        return {"InternetGateway": copy.deepcopy(self.w.igws[gid])}

    def attach_internet_gateway(self, InternetGatewayId, VpcId):
        self.w.igws[InternetGatewayId]["Attachments"] = [{"State": "available", "VpcId": VpcId}]
        return {}

    def detach_internet_gateway(self, InternetGatewayId, VpcId):
        self.w.igws[InternetGatewayId]["Attachments"] = []
        return {}

    def delete_internet_gateway(self, InternetGatewayId):
        del self.w.igws[InternetGatewayId]
        return {}

    def describe_internet_gateways(self, Filters=None):
        out = []
        for g in self.w.igws.values():
            attached = [a["VpcId"] for a in g["Attachments"]]
            flt = [f for f in Filters or [] if f["Name"] != "attachment.vpc-id"]
            by_vpc = [f for f in Filters or [] if f["Name"] == "attachment.vpc-id"]
            if match_filters(g["Tags"], {}, flt) and all(any(v in attached for v in f["Values"]) for f in by_vpc):
                out.append(copy.deepcopy(g))
        return {"InternetGateways": out}

    # security groups
    def create_security_group(self, GroupName, Description, VpcId, TagSpecifications):
        gid = f"sg-{self.w.hex()}"
        self.w.sgs[gid] = {"GroupId": gid, "GroupName": GroupName, "VpcId": VpcId, "Tags": tags_of(TagSpecifications)}
        rid = f"sgr-{self.w.hex()}"
        self.w.sg_rules[rid] = {
            "SecurityGroupRuleId": rid,
            "GroupId": gid,
            "IsEgress": True,
            "IpProtocol": "-1",
            "FromPort": -1,
            "ToPort": -1,
            "CidrIpv4": "0.0.0.0/0",
        }
        return {"GroupId": gid}

    def describe_security_groups(self, Filters=None):
        return {
            "SecurityGroups": [
                copy.deepcopy(g)
                for g in self.w.sgs.values()
                if match_filters(g["Tags"], {"vpc-id": g["VpcId"], "group-name": g["GroupName"]}, Filters)
            ]
        }

    def describe_security_group_rules(self, Filters):
        gid = Filters[0]["Values"][0]
        return {"SecurityGroupRules": [copy.deepcopy(r) for r in self.w.sg_rules.values() if r["GroupId"] == gid]}

    def _authorize(self, GroupId, IpPermissions, egress):
        for p in IpPermissions:
            rid = f"sgr-{self.w.hex()}"
            r = {
                "SecurityGroupRuleId": rid,
                "GroupId": GroupId,
                "IsEgress": egress,
                "IpProtocol": p["IpProtocol"],
                "FromPort": p.get("FromPort", -1),
                "ToPort": p.get("ToPort", -1),
            }
            if p.get("IpRanges"):
                r["CidrIpv4"] = p["IpRanges"][0]["CidrIp"]
            if p.get("UserIdGroupPairs"):
                r["ReferencedGroupInfo"] = {"GroupId": p["UserIdGroupPairs"][0]["GroupId"]}
            self.w.sg_rules[rid] = r
        return {"Return": True}

    def authorize_security_group_egress(self, GroupId, IpPermissions):
        return self._authorize(GroupId, IpPermissions, True)

    def authorize_security_group_ingress(self, GroupId, IpPermissions):
        return self._authorize(GroupId, IpPermissions, False)

    def revoke_security_group_egress(self, GroupId, SecurityGroupRuleIds=None, IpPermissions=None):
        for r in SecurityGroupRuleIds or []:
            if r not in self.w.sg_rules:
                raise error("InvalidSecurityGroupRuleId.NotFound", "RevokeSecurityGroupEgress")
            del self.w.sg_rules[r]
        unknown = []
        for p in IpPermissions or []:
            cidr = (p.get("IpRanges") or [{}])[0].get("CidrIp")
            hit = [
                rid
                for rid, r in self.w.sg_rules.items()
                if r["GroupId"] == GroupId
                and r["IsEgress"]
                and r["IpProtocol"] == p["IpProtocol"]
                and r.get("FromPort", -1) == p.get("FromPort", -1)
                and r.get("CidrIpv4") == cidr
            ]
            for rid in hit:
                del self.w.sg_rules[rid]
            if not hit:
                unknown.append(p)
        return {"Return": True, **({"UnknownIpPermissions": unknown} if unknown else {})}

    def revoke_security_group_ingress(self, GroupId, SecurityGroupRuleIds):
        return self.revoke_security_group_egress(GroupId, SecurityGroupRuleIds)

    def delete_security_group(self, GroupId):
        if [
            r
            for r in self.w.sg_rules.values()
            if r["GroupId"] != GroupId and (r.get("ReferencedGroupInfo") or {}).get("GroupId") == GroupId
        ]:
            raise error("DependencyViolation", "DeleteSecurityGroup", f"resource {GroupId} has a dependent object")
        if [m for m, sgs in self.w.efs_mt_sgs.items() if GroupId in sgs and m in self.w.efs_mts]:
            raise error(
                "DependencyViolation",
                "DeleteSecurityGroup",
                f"resource {GroupId} has a dependent object (a network interface)",
            )
        del self.w.sgs[GroupId]
        self.w.sg_rules = {k: v for k, v in self.w.sg_rules.items() if v["GroupId"] != GroupId}
        return {}

    # addresses and NAT
    def allocate_address(self, Domain, TagSpecifications):
        aid = f"eipalloc-{self.w.hex()}"
        self.w.addresses[aid] = {
            "AllocationId": aid,
            "PublicIp": "198.51.100.7",
            "Domain": Domain,
            "Tags": tags_of(TagSpecifications),
        }
        return {"AllocationId": aid, "PublicIp": "198.51.100.7"}

    def describe_addresses(self, Filters=None):
        return {
            "Addresses": [copy.deepcopy(a) for a in self.w.addresses.values() if match_filters(a["Tags"], {}, Filters)]
        }

    def release_address(self, AllocationId):
        assert not [n for n in self.w.nats.values() if n["AllocationId"] == AllocationId and n["State"] != "deleted"], (
            "address still used"
        )
        del self.w.addresses[AllocationId]
        return {}

    def create_nat_gateway(self, SubnetId, AllocationId, ConnectivityType, TagSpecifications):
        nid = f"nat-{self.w.hex()}"
        self.w.nats[nid] = {
            "NatGatewayId": nid,
            "SubnetId": SubnetId,
            "VpcId": self.w.subnets[SubnetId]["VpcId"],
            "State": "pending",
            "AllocationId": AllocationId,
            "Tags": tags_of(TagSpecifications),
        }
        return {"NatGateway": copy.deepcopy(self.w.nats[nid])}

    def describe_nat_gateways(self, Filter=None, NatGatewayIds=None):
        return {
            "NatGateways": [
                copy.deepcopy(n)
                for n in self.w.nats.values()
                if match_filters(n["Tags"], {"vpc-id": n["VpcId"], "state": n["State"]}, Filter)
                and (NatGatewayIds is None or n["NatGatewayId"] in NatGatewayIds)
            ]
        }

    def delete_nat_gateway(self, NatGatewayId):
        self.w.nats[NatGatewayId]["State"] = "deleted"
        self._blackhole(NatGatewayId)
        return {"NatGatewayId": NatGatewayId}

    def _blackhole(self, target):
        for rt in self.w.rts.values():
            for r in rt["Routes"]:
                if target in (r.get("GatewayId"), r.get("NatGatewayId")):
                    r["State"] = "blackhole"

    # route tables
    def describe_network_interfaces(self, Filters=None, **kw):
        vpc = next((f["Values"] for f in Filters or [] if f["Name"] == "vpc-id"), None)
        return {"NetworkInterfaces": [copy.deepcopy(n) for n in self.w.enis.values() if not vpc or n["VpcId"] in vpc]}

    def create_route_table(self, VpcId, TagSpecifications):
        rid = f"rtb-{self.w.hex()}"
        self.w.rts[rid] = {
            "RouteTableId": rid,
            "VpcId": VpcId,
            "Tags": tags_of(TagSpecifications),
            "Associations": [],
            "Routes": [
                {"DestinationCidrBlock": self.w.vpcs[VpcId]["CidrBlock"], "GatewayId": "local", "State": "active"}
            ],
        }
        return {"RouteTable": copy.deepcopy(self.w.rts[rid])}

    def describe_route_tables(self, Filters=None):
        return {
            "RouteTables": [
                copy.deepcopy(r)
                for r in self.w.rts.values()
                if match_filters(r["Tags"], {"vpc-id": r["VpcId"]}, Filters)
            ]
        }

    def associate_route_table(self, RouteTableId, SubnetId):
        aid = f"rtbassoc-{self.w.hex()}"
        self.w.rts[RouteTableId]["Associations"].append(
            {"RouteTableAssociationId": aid, "SubnetId": SubnetId, "Main": False}
        )
        return {"AssociationId": aid}

    def disassociate_route_table(self, AssociationId):
        for rt in self.w.rts.values():
            rt["Associations"] = [a for a in rt["Associations"] if a["RouteTableAssociationId"] != AssociationId]
        return {}

    def delete_route_table(self, RouteTableId):
        assert not [e for e in self.w.endpoints.values() if RouteTableId in e["RouteTableIds"]], (
            "a VPC endpoint still routes through it"
        )
        del self.w.rts[RouteTableId]
        return {}

    def _route(self, RouteTableId, DestinationCidrBlock, replace, **target):
        rt = self.w.rts[RouteTableId]
        cur = [r for r in rt["Routes"] if r.get("DestinationCidrBlock") == DestinationCidrBlock]
        assert bool(cur) == replace, (
            f"{'replace' if replace else 'create'} route {DestinationCidrBlock} in {RouteTableId}"
        )
        ((kind, tid),) = target.items()
        route = {
            "DestinationCidrBlock": DestinationCidrBlock,
            "State": "active",
            ("GatewayId" if kind in ("VpcEndpointId", "GatewayId") else kind): tid,
        }
        rt["Routes"] = [r for r in rt["Routes"] if r.get("DestinationCidrBlock") != DestinationCidrBlock] + [route]
        return {"Return": True}

    def create_route(self, RouteTableId, DestinationCidrBlock, **target):
        return self._route(RouteTableId, DestinationCidrBlock, False, **target)

    def replace_route(self, RouteTableId, DestinationCidrBlock, **target):
        return self._route(RouteTableId, DestinationCidrBlock, True, **target)

    def delete_route(self, RouteTableId, DestinationCidrBlock):
        rt = self.w.rts[RouteTableId]
        assert [r for r in rt["Routes"] if r.get("DestinationCidrBlock") == DestinationCidrBlock], (
            f"no route {DestinationCidrBlock}"
        )
        rt["Routes"] = [r for r in rt["Routes"] if r.get("DestinationCidrBlock") != DestinationCidrBlock]
        return {}


class Iam(Svc):
    def _role(self, name):
        if name not in self.w.roles:
            raise error("NoSuchEntity", "GetRole")
        return self.w.roles[name]

    def get_role(self, RoleName):
        r = self._role(RoleName)
        out = {
            "RoleName": RoleName,
            "Arn": f"arn:aws:iam::{ACCOUNT}:role/{RoleName}",
            "AssumeRolePolicyDocument": copy.deepcopy(r["trust"]),
        }
        if r.get("boundary"):
            out["PermissionsBoundary"] = {"PermissionsBoundaryType": "Policy", "PermissionsBoundaryArn": r["boundary"]}
        return {"Role": out}

    def create_role(self, RoleName, AssumeRolePolicyDocument, PermissionsBoundary=None, **kw):
        if RoleName in self.w.roles:
            raise error("EntityAlreadyExists", "CreateRole", f"Role with name {RoleName} already exists.", 409)
        if PermissionsBoundary and PermissionsBoundary not in self.w.managed_policies:
            raise error("NoSuchEntity", "CreateRole", f"Policy {PermissionsBoundary} does not exist", 404)
        self.w.roles[RoleName] = {
            "trust": json.loads(AssumeRolePolicyDocument),
            "managed": [],
            "inline": {},
            "boundary": PermissionsBoundary,
        }
        return {"Role": {"RoleName": RoleName, "Arn": f"arn:aws:iam::{ACCOUNT}:role/{RoleName}"}}

    def list_roles(self, **kw):
        return {
            "Roles": [{"RoleName": n, "Arn": f"arn:aws:iam::{ACCOUNT}:role/{n}"} for n in sorted(self.w.roles)],
            "IsTruncated": False,
        }

    def _policy(self, PolicyArn, op):
        if PolicyArn not in self.w.managed_policies:
            raise error("NoSuchEntity", op, f"Policy {PolicyArn} was not found.", 404)
        return self.w.managed_policies[PolicyArn]

    def create_policy(self, PolicyName, PolicyDocument, **kw):
        arn = f"arn:aws:iam::{ACCOUNT}:policy/{PolicyName}"
        assert arn not in self.w.managed_policies
        self.w.managed_policies[arn] = {"versions": {"v1": json.loads(PolicyDocument)}, "default": "v1", "n": 1}
        return {"Policy": {"PolicyName": PolicyName, "Arn": arn, "DefaultVersionId": "v1"}}

    def get_policy(self, PolicyArn):
        p = self._policy(PolicyArn, "GetPolicy")
        return {
            "Policy": {"Arn": PolicyArn, "PolicyName": PolicyArn.rsplit("/", 1)[-1], "DefaultVersionId": p["default"]}
        }

    def get_policy_version(self, PolicyArn, VersionId):
        p = self._policy(PolicyArn, "GetPolicyVersion")
        return {
            "PolicyVersion": {
                "Document": copy.deepcopy(p["versions"][VersionId]),
                "VersionId": VersionId,
                "IsDefaultVersion": VersionId == p["default"],
            }
        }

    def list_policy_versions(self, PolicyArn, **kw):
        p = self._policy(PolicyArn, "ListPolicyVersions")
        return {
            "Versions": [
                {
                    "VersionId": v,
                    "IsDefaultVersion": v == p["default"],
                    "CreateDate": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
                    + datetime.timedelta(days=int(v[1:])),
                }
                for v in p["versions"]
            ]
        }

    def create_policy_version(self, PolicyArn, PolicyDocument, SetAsDefault=False):
        p = self._policy(PolicyArn, "CreatePolicyVersion")
        assert len(p["versions"]) < 5, "IAM keeps at most 5 versions"
        p["n"] += 1
        v = f"v{p['n']}"
        p["versions"][v] = json.loads(PolicyDocument)
        if SetAsDefault:
            p["default"] = v
        return {"PolicyVersion": {"VersionId": v, "IsDefaultVersion": SetAsDefault}}

    def delete_policy_version(self, PolicyArn, VersionId):
        p = self._policy(PolicyArn, "DeletePolicyVersion")
        assert VersionId != p["default"]
        del p["versions"][VersionId]
        return {}

    def delete_policy(self, PolicyArn):
        p = self._policy(PolicyArn, "DeletePolicy")
        assert list(p["versions"]) == [p["default"]], "delete the other versions first"
        assert not [n for n, r in self.w.roles.items() if r.get("boundary") == PolicyArn], (
            "still a role's permissions boundary"
        )
        del self.w.managed_policies[PolicyArn]
        return {}

    def update_assume_role_policy(self, RoleName, PolicyDocument):
        self._role(RoleName)["trust"] = json.loads(PolicyDocument)
        return {}

    def attach_role_policy(self, RoleName, PolicyArn):
        self._role(RoleName)["managed"].append(PolicyArn)
        return {}

    def detach_role_policy(self, RoleName, PolicyArn):
        self._role(RoleName)["managed"].remove(PolicyArn)
        return {}

    def list_attached_role_policies(self, RoleName, **kw):
        return {
            "AttachedPolicies": [
                {"PolicyArn": a, "PolicyName": a.rsplit("/", 1)[-1]} for a in self._role(RoleName)["managed"]
            ],
            "IsTruncated": False,
        }

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        self._role(RoleName)["inline"][PolicyName] = json.loads(PolicyDocument)
        return {}

    def get_role_policy(self, RoleName, PolicyName):
        return {
            "RoleName": RoleName,
            "PolicyName": PolicyName,
            "PolicyDocument": copy.deepcopy(self._role(RoleName)["inline"][PolicyName]),
        }

    def list_role_policies(self, RoleName, **kw):
        return {"PolicyNames": list(self._role(RoleName)["inline"]), "IsTruncated": False}

    def delete_role_policy(self, RoleName, PolicyName):
        del self._role(RoleName)["inline"][PolicyName]
        return {}

    def delete_role(self, RoleName):
        r = self._role(RoleName)
        assert not r["managed"] and not r["inline"], f"{RoleName} still has policies"
        assert not [p for p in self.w.profiles.values() if RoleName in p], f"{RoleName} still in an instance profile"
        del self.w.roles[RoleName]
        # IAM keeps a resource policy's principal as the role's unique id: once the role is gone the policy shows that id
        # (AROA…), and a new role with the same name doesn't match it. So the policy has to be put again after a redeploy.
        arn = f"arn:aws:iam::{ACCOUNT}:role/{RoleName}"
        for fid, pol in self.w.efs_policies.items():
            self.w.efs_policies[fid] = pol.replace(json.dumps(arn), json.dumps(f"AROA{self.w.hex(17).upper()}"))
        return {}

    def get_instance_profile(self, InstanceProfileName):
        if InstanceProfileName not in self.w.profiles:
            raise error("NoSuchEntity", "GetInstanceProfile")
        return {
            "InstanceProfile": {
                "InstanceProfileName": InstanceProfileName,
                "Roles": [{"RoleName": r} for r in self.w.profiles[InstanceProfileName]],
            }
        }

    def create_instance_profile(self, InstanceProfileName, **kw):
        self.w.profiles[InstanceProfileName] = []
        return {"InstanceProfile": {"InstanceProfileName": InstanceProfileName}}

    def add_role_to_instance_profile(self, InstanceProfileName, RoleName):
        self.w.profiles[InstanceProfileName].append(RoleName)
        return {}

    def list_instance_profiles_for_role(self, RoleName, **kw):
        return {
            "InstanceProfiles": [
                {"InstanceProfileName": p} for p, roles in self.w.profiles.items() if RoleName in roles
            ],
            "IsTruncated": False,
        }

    def remove_role_from_instance_profile(self, InstanceProfileName, RoleName):
        self.w.profiles[InstanceProfileName].remove(RoleName)
        return {}

    def delete_instance_profile(self, InstanceProfileName):
        del self.w.profiles[InstanceProfileName]
        return {}


class Ecr(Svc):
    def describe_repositories(self, repositoryNames):
        if repositoryNames[0] not in self.w.repos:
            raise error("RepositoryNotFoundException", "DescribeRepositories")
        return {"repositories": [{"repositoryName": repositoryNames[0]}]}

    def create_repository(self, repositoryName, **kw):
        self.w.repos[repositoryName] = {}
        return {"repository": {"repositoryName": repositoryName}}

    def describe_images(self, repositoryName, imageIds):
        tag = imageIds[0]["imageTag"]
        if tag not in self.w.repos.get(repositoryName, {}):
            raise error("ImageNotFoundException", "DescribeImages")
        return {"imageDetails": [{"imageTags": [tag], "imageSizeInBytes": self.w.repos[repositoryName][tag]}]}

    def get_authorization_token(self):
        return {
            "authorizationData": [
                {
                    "authorizationToken": base64.b64encode(b"AWS:secret").decode(),
                    "proxyEndpoint": f"https://{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com",
                }
            ]
        }

    def delete_repository(self, repositoryName, force):
        del self.w.repos[repositoryName]
        return {}


class Logs(Svc):
    def describe_log_groups(self, logGroupNamePrefix, **kw):
        return {
            "logGroups": [dict(g) for n, g in sorted(self.w.log_groups.items()) if n.startswith(logGroupNamePrefix)]
        }

    def create_log_group(self, logGroupName, **kw):
        self.w.log_groups[logGroupName] = {"logGroupName": logGroupName}
        return {}

    def put_retention_policy(self, logGroupName, retentionInDays):
        self.w.log_groups[logGroupName]["retentionInDays"] = retentionInDays
        return {}

    def delete_log_group(self, logGroupName):
        del self.w.log_groups[logGroupName]
        return {}

    def start_query(self, logGroupName, **kw):
        return {"queryId": f"q:{logGroupName}"}

    def get_query_results(self, queryId):
        group = queryId.split(":", 1)[1]
        if group == "/devbox/dns-queries":
            rows = [
                [{"field": "query_name", "value": n + "."}, {"field": "hits", "value": "2"}]
                for n in self.w.dns_query_names
            ]
        else:
            rows = [[{"field": "name", "value": n}, {"field": "hits", "value": "3"}] for n in self.w.query_names]
            rows += [
                [{"field": "name", "value": n}, {"field": "http_host", "value": n}, {"field": "hits", "value": "1"}]
                for n in self.w.query_http_names
            ]
        return {"status": "Complete", "results": rows}


class Nfw(Svc):
    def _arn(self, kind, name):
        return f"arn:aws:network-firewall:{REGION}:{ACCOUNT}:{kind}/{name}"

    def describe_rule_group(self, RuleGroupName, Type):
        g = self.w.rule_groups.get(RuleGroupName)
        if not g:
            raise error("ResourceNotFoundException", "DescribeRuleGroup")
        return copy.deepcopy(g)

    def create_rule_group(self, RuleGroupName, Type, Capacity, RuleGroup, **kw):
        resp = {
            "RuleGroupArn": self._arn("stateful-rulegroup", RuleGroupName),
            "RuleGroupName": RuleGroupName,
            "RuleGroupId": self.w.token(),
        }
        self.w.rule_groups[RuleGroupName] = {
            "UpdateToken": self.w.token(),
            "RuleGroup": copy.deepcopy(RuleGroup),
            "RuleGroupResponse": resp,
        }
        return {"UpdateToken": self.w.rule_groups[RuleGroupName]["UpdateToken"], "RuleGroupResponse": resp}

    def update_rule_group(self, UpdateToken, RuleGroupArn, Type, RuleGroup):
        g = next(g for g in self.w.rule_groups.values() if g["RuleGroupResponse"]["RuleGroupArn"] == RuleGroupArn)
        assert g["UpdateToken"] == UpdateToken
        g["RuleGroup"], g["UpdateToken"] = copy.deepcopy(RuleGroup), self.w.token()
        return {"UpdateToken": g["UpdateToken"], "RuleGroupResponse": g["RuleGroupResponse"]}

    def delete_rule_group(self, RuleGroupArn):
        lag = getattr(self.w, "rg_lag", {})
        if lag.get(RuleGroupArn):  # live 2026-10-02: still "in use" for a moment after the policy that used it is gone
            lag[RuleGroupArn] -= 1
            raise error(
                "InvalidOperationException", "DeleteRuleGroup", "Unable to delete the object because it is still in use"
            )
        name = next(n for n, g in self.w.rule_groups.items() if g["RuleGroupResponse"]["RuleGroupArn"] == RuleGroupArn)
        del self.w.rule_groups[name]
        return {"RuleGroupResponse": {}}

    def describe_firewall_policy(self, FirewallPolicyName):
        p = self.w.fw_policies.get(FirewallPolicyName)
        if not p:
            raise error("ResourceNotFoundException", "DescribeFirewallPolicy")
        out = copy.deepcopy(p)
        out["FirewallPolicy"].setdefault("StatefulEngineOptions", {})["StreamExceptionPolicy"] = (
            "DROP"  # a service default
        )
        return out

    def create_firewall_policy(self, FirewallPolicyName, FirewallPolicy, **kw):
        resp = {
            "FirewallPolicyArn": self._arn("firewall-policy", FirewallPolicyName),
            "FirewallPolicyName": FirewallPolicyName,
            "FirewallPolicyId": self.w.token(),
        }
        self.w.fw_policies[FirewallPolicyName] = {
            "UpdateToken": self.w.token(),
            "FirewallPolicy": copy.deepcopy(FirewallPolicy),
            "FirewallPolicyResponse": resp,
        }
        return {"UpdateToken": self.w.fw_policies[FirewallPolicyName]["UpdateToken"], "FirewallPolicyResponse": resp}

    def update_firewall_policy(self, UpdateToken, FirewallPolicyArn, FirewallPolicy):
        p = next(
            p
            for p in self.w.fw_policies.values()
            if p["FirewallPolicyResponse"]["FirewallPolicyArn"] == FirewallPolicyArn
        )
        assert p["UpdateToken"] == UpdateToken
        p["FirewallPolicy"], p["UpdateToken"] = copy.deepcopy(FirewallPolicy), self.w.token()
        return {"UpdateToken": p["UpdateToken"], "FirewallPolicyResponse": p["FirewallPolicyResponse"]}

    def delete_firewall_policy(self, FirewallPolicyName):
        assert not self.w.firewall, "the firewall still uses the policy"
        doc = json.dumps(self.w.fw_policies[FirewallPolicyName], default=str)
        self.w.rg_lag = {
            g["RuleGroupResponse"]["RuleGroupArn"]: 1
            for g in self.w.rule_groups.values()
            if g["RuleGroupResponse"]["RuleGroupArn"] in doc
        }
        del self.w.fw_policies[FirewallPolicyName]
        return {"FirewallPolicyResponse": {}}

    def create_firewall(self, FirewallName, FirewallPolicyArn, VpcId, SubnetMappings, **kw):
        assert self.w.firewall is None
        self.w.firewall = {
            "name": FirewallName,
            "policy": FirewallPolicyArn,
            "vpc": VpcId,
            "subnet": SubnetMappings[0]["SubnetId"],
            "describes": 0,
            "endpoint": f"vpce-{self.w.hex()}",
            "token": self.w.token(),
        }
        return {"Firewall": {"FirewallName": FirewallName}}

    def describe_firewall(self, FirewallName):
        f = self.w.firewall
        if not f:
            raise error("ResourceNotFoundException", "DescribeFirewall")
        f["describes"] += 1
        ready = f["describes"] > 1 and not f.get("deleting")
        status = {
            "Status": "DELETING" if f.get("deleting") else ("READY" if ready else "PROVISIONING"),
            "ConfigurationSyncStateSummary": "IN_SYNC" if ready else "PENDING",
        }
        if ready:
            status["SyncStates"] = {
                "us-east-1a": {"Attachment": {"SubnetId": f["subnet"], "EndpointId": f["endpoint"], "Status": "READY"}}
            }
        if f.get("deleting"):
            self.w.firewall = None  # the next describe finds nothing
        return {
            "UpdateToken": f["token"],
            "Firewall": {
                "FirewallName": f["name"],
                "FirewallPolicyArn": f["policy"],
                "VpcId": f["vpc"],
                "SubnetMappings": [{"SubnetId": f["subnet"]}],
                "FirewallId": f["token"],
            },
            "FirewallStatus": status,
        }

    def associate_firewall_policy(self, FirewallName, FirewallPolicyArn, UpdateToken):
        self.w.firewall["policy"] = FirewallPolicyArn
        return {}

    def delete_firewall(self, FirewallName):
        ep = self.w.firewall["endpoint"]
        if [r for rt in self.w.rts.values() for r in rt["Routes"] if r.get("GatewayId") == ep]:
            # live 2026-10-02: AWS refuses while any route table still points at the firewall's endpoint
            raise error(
                "InvalidRequestException",
                "DeleteFirewall",
                "Unable to fulfill request because the following related VPC "
                f"endpoint(s) still exist in route table(s): [{ep}]",
            )
        if self.w.fw_logging:  # live 2026-10-02
            raise error(
                "InvalidRequestException", "DeleteFirewall", "Cannot delete firewall with a logging configuration"
            )
        self.w.firewall["deleting"] = True
        Ec2(self.w)._blackhole(self.w.firewall["endpoint"])
        return {"Firewall": {"FirewallName": FirewallName}}

    def describe_logging_configuration(self, FirewallName):
        return {"LoggingConfiguration": {"LogDestinationConfigs": copy.deepcopy(self.w.fw_logging)}}

    def update_logging_configuration(self, FirewallName, LoggingConfiguration):
        new = LoggingConfiguration["LogDestinationConfigs"]
        assert abs(len(new) - len(self.w.fw_logging)) <= 1, (
            "Network Firewall adds or removes one log destination per call"
        )
        self.w.fw_logging = copy.deepcopy(new)
        return {}


class Resolver(Svc):
    """Route 53 Resolver DNS Firewall and query logging. Domains come back with a trailing dot, as the service stores them."""

    DOMAIN = re.compile(r"\*|(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")

    def _id(self, kind):
        return f"rslvr-{kind}-{self.w.hex()}"

    def _arn(self, kind, rid):
        return f"arn:aws:route53resolver:{REGION}:{ACCOUNT}:{kind}/{rid}"

    # domain lists
    def list_firewall_domain_lists(self, **kw):
        return {
            "FirewallDomainLists": [
                {
                    "Id": i,
                    "Arn": self._arn("firewall-domain-list", i),
                    "Name": d["Name"],
                    "CreatorRequestId": d["CreatorRequestId"],
                }
                for i, d in self.w.dns_lists.items()
            ]
        }

    def create_firewall_domain_list(self, CreatorRequestId, Name, Tags=None):
        assert Name not in {d["Name"] for d in self.w.dns_lists.values()}, f"a domain list {Name} exists"
        lid = self._id("fdl")
        self.w.dns_lists[lid] = {"Name": Name, "CreatorRequestId": CreatorRequestId, "Domains": []}
        return {"FirewallDomainList": {"Id": lid, "Name": Name, "DomainCount": 0, "Status": "COMPLETE"}}

    def update_firewall_domains(self, FirewallDomainListId, Operation, Domains):
        assert Operation == "REPLACE" and len(Domains) <= 1000
        for d in Domains:
            assert self.DOMAIN.fullmatch(d), f"not a DNS Firewall domain: {d!r}"
        self.w.dns_lists[FirewallDomainListId]["Domains"] = [d + "." for d in Domains]
        return {
            "Id": FirewallDomainListId,
            "Name": self.w.dns_lists[FirewallDomainListId]["Name"],
            "Status": "UPDATING",
        }

    def list_firewall_domains(self, FirewallDomainListId, **kw):
        return {"Domains": list(self.w.dns_lists[FirewallDomainListId]["Domains"])}

    def delete_firewall_domain_list(self, FirewallDomainListId):
        assert not [r for r in self.w.dns_rules.values() if r["FirewallDomainListId"] == FirewallDomainListId], (
            "a rule uses the list"
        )
        del self.w.dns_lists[FirewallDomainListId]
        return {"FirewallDomainList": {"Id": FirewallDomainListId, "Status": "DELETING"}}

    # rule groups and rules
    def list_firewall_rule_groups(self, **kw):
        return {
            "FirewallRuleGroups": [
                {"Id": i, "Arn": self._arn("firewall-rule-group", i), "Name": g["Name"], "OwnerId": ACCOUNT}
                for i, g in self.w.dns_groups.items()
            ]
        }

    def create_firewall_rule_group(self, CreatorRequestId, Name, Tags=None):
        gid = self._id("frg")
        self.w.dns_groups[gid] = {"Name": Name}
        return {"FirewallRuleGroup": {"Id": gid, "Name": Name, "RuleCount": 0, "Status": "COMPLETE"}}

    def delete_firewall_rule_group(self, FirewallRuleGroupId):
        assert not [r for r in self.w.dns_rules.values() if r["FirewallRuleGroupId"] == FirewallRuleGroupId], (
            "the group still has rules"
        )
        assert not [a for a in self.w.dns_assocs.values() if a["FirewallRuleGroupId"] == FirewallRuleGroupId], (
            "the group is still on a VPC"
        )
        del self.w.dns_groups[FirewallRuleGroupId]
        return {"FirewallRuleGroup": {"Id": FirewallRuleGroupId, "Status": "DELETING"}}

    def list_firewall_rules(self, FirewallRuleGroupId, **kw):
        return {
            "FirewallRules": [
                copy.deepcopy(r) for r in self.w.dns_rules.values() if r["FirewallRuleGroupId"] == FirewallRuleGroupId
            ]
        }

    def create_firewall_rule(
        self, CreatorRequestId, FirewallRuleGroupId, Priority, Action, Name, FirewallDomainListId, **kw
    ):
        assert FirewallRuleGroupId in self.w.dns_groups and FirewallDomainListId in self.w.dns_lists
        assert Action != "BLOCK" or kw.get("BlockResponse"), "a BLOCK rule needs a BlockResponse"
        mine = [r for r in self.w.dns_rules.values() if r["FirewallRuleGroupId"] == FirewallRuleGroupId]
        assert Priority not in {r["Priority"] for r in mine} and FirewallDomainListId not in {
            r["FirewallDomainListId"] for r in mine
        }
        rule = {
            "FirewallRuleGroupId": FirewallRuleGroupId,
            "FirewallDomainListId": FirewallDomainListId,
            "Name": Name,
            "Priority": Priority,
            "Action": Action,
            "FirewallDomainRedirectionAction": "INSPECT_REDIRECTION_DOMAIN",
            **kw,
        }
        self.w.dns_rules[(FirewallRuleGroupId, FirewallDomainListId)] = rule
        return {"FirewallRule": copy.deepcopy(rule)}

    def update_firewall_rule(self, FirewallRuleGroupId, FirewallDomainListId, **kw):
        rule = self.w.dns_rules[(FirewallRuleGroupId, FirewallDomainListId)]
        rule.update(kw)
        assert rule["Action"] != "BLOCK" or rule.get("BlockResponse"), "a BLOCK rule needs a BlockResponse"
        return {"FirewallRule": copy.deepcopy(rule)}

    def delete_firewall_rule(self, FirewallRuleGroupId, FirewallDomainListId):
        return {"FirewallRule": self.w.dns_rules.pop((FirewallRuleGroupId, FirewallDomainListId))}

    # the VPC
    def associate_firewall_rule_group(self, CreatorRequestId, FirewallRuleGroupId, VpcId, Priority, Name, **kw):
        assert VpcId in self.w.vpcs and FirewallRuleGroupId in self.w.dns_groups and 100 <= Priority <= 9900
        assert not [
            a
            for a in self.w.dns_assocs.values()
            if a["VpcId"] == VpcId and a["FirewallRuleGroupId"] == FirewallRuleGroupId
        ]
        aid = self._id("frgassoc")
        self.w.dns_assocs[aid] = {
            "Id": aid,
            "FirewallRuleGroupId": FirewallRuleGroupId,
            "VpcId": VpcId,
            "Priority": Priority,
            "Name": Name,
            "MutationProtection": kw.get("MutationProtection", "DISABLED"),
            "Status": "UPDATING",
        }
        return {"FirewallRuleGroupAssociation": copy.deepcopy(self.w.dns_assocs[aid])}

    def list_firewall_rule_group_associations(self, FirewallRuleGroupId=None, VpcId=None, **kw):
        out = []
        for a in self.w.dns_assocs.values():
            if (FirewallRuleGroupId in (None, a["FirewallRuleGroupId"])) and (VpcId in (None, a["VpcId"])):
                out.append(copy.deepcopy(a))
                a["Status"] = "COMPLETE"
        return {"FirewallRuleGroupAssociations": out}

    def disassociate_firewall_rule_group(self, FirewallRuleGroupAssociationId):
        return {"FirewallRuleGroupAssociation": self.w.dns_assocs.pop(FirewallRuleGroupAssociationId)}

    def get_firewall_config(self, ResourceId):
        return {
            "FirewallConfig": {
                "Id": f"rslvr-fc-{ResourceId[4:]}",
                "ResourceId": ResourceId,
                "OwnerId": ACCOUNT,
                "FirewallFailOpen": self.w.dns_fail_open.get(ResourceId, "DISABLED"),
            }
        }

    def update_firewall_config(self, ResourceId, FirewallFailOpen):
        self.w.dns_fail_open[ResourceId] = FirewallFailOpen
        return self.get_firewall_config(ResourceId)

    # query logging
    def list_resolver_query_log_configs(self, **kw):
        return {"ResolverQueryLogConfigs": [copy.deepcopy(c) for c in self.w.qlcs.values()]}

    def create_resolver_query_log_config(self, Name, DestinationArn, CreatorRequestId, Tags=None):
        m = re.fullmatch(rf"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:(.+?)(:\*)?", DestinationArn)
        assert m and m.group(1) in self.w.log_groups, f"no log group for {DestinationArn}"
        qid = self._id("rqlc")
        self.w.qlcs[qid] = {
            "Id": qid,
            "Name": Name,
            "DestinationArn": DestinationArn,
            "Status": "CREATING",
            "OwnerId": ACCOUNT,
            "Arn": self._arn("resolver-query-log-config", qid),
            "CreatorRequestId": CreatorRequestId,
        }
        return {"ResolverQueryLogConfig": copy.deepcopy(self.w.qlcs[qid])}

    def get_resolver_query_log_config(self, ResolverQueryLogConfigId):
        c = self.w.qlcs[ResolverQueryLogConfigId]
        out = copy.deepcopy(c)
        c["Status"] = "CREATED"
        return {"ResolverQueryLogConfig": out}

    def delete_resolver_query_log_config(self, ResolverQueryLogConfigId):
        assert not [
            a for a in self.w.qlc_assocs.values() if a["ResolverQueryLogConfigId"] == ResolverQueryLogConfigId
        ], "still associated"
        return {"ResolverQueryLogConfig": self.w.qlcs.pop(ResolverQueryLogConfigId)}

    def associate_resolver_query_log_config(self, ResolverQueryLogConfigId, ResourceId):
        assert self.w.qlcs[ResolverQueryLogConfigId]["Status"] == "CREATED", "query logging isn't created yet"
        aid = self._id("rqlca")
        self.w.qlc_assocs[aid] = {
            "Id": aid,
            "ResolverQueryLogConfigId": ResolverQueryLogConfigId,
            "ResourceId": ResourceId,
            "Status": "CREATING",
        }
        return {"ResolverQueryLogConfigAssociation": copy.deepcopy(self.w.qlc_assocs[aid])}

    def get_resolver_query_log_config_association(self, ResolverQueryLogConfigAssociationId):
        a = self.w.qlc_assocs[ResolverQueryLogConfigAssociationId]
        if a["Status"] == "CREATING":
            a["Status"] = self.w.qlc_assoc_outcome
            if a["Status"] == "ACTION_NEEDED":
                a.update(Error="ACCESS_DENIED", ErrorMessage="can't write to the log group")
        return {"ResolverQueryLogConfigAssociation": copy.deepcopy(a)}

    def list_resolver_query_log_config_associations(self, Filters=None, **kw):
        out = []
        for a in self.w.qlc_assocs.values():
            if all(a.get(f["Name"]) in f["Values"] for f in Filters or []):
                out.append(copy.deepcopy(a))
        return {"ResolverQueryLogConfigAssociations": out}

    def disassociate_resolver_query_log_config(self, ResolverQueryLogConfigId, ResourceId):
        aid = next(
            i
            for i, a in self.w.qlc_assocs.items()
            if a["ResolverQueryLogConfigId"] == ResolverQueryLogConfigId and a["ResourceId"] == ResourceId
        )
        return {"ResolverQueryLogConfigAssociation": self.w.qlc_assocs.pop(aid)}


class Lambda(Svc):
    """Two functions: the edge (an image; self.w.fn, self.w.fn_url, self.w.fn_statements, as the tests read it) and the
    provisioner (a zip; self.w.fns, self.w.fn_policies), told apart by name."""

    EDGE = "devbox-edge"

    def _fn(self, name=EDGE, op="GetFunction"):
        f = self.w.fn if name == self.EDGE else self.w.fns.get(name)
        if not f:
            raise error("ResourceNotFoundException", op)
        return f

    def _statements(self, name):
        return self.w.fn_statements if name == self.EDGE else self.w.fn_policies.setdefault(name, [])

    def get_function(self, FunctionName):
        f = self._fn(FunctionName)
        conf = {k: v for k, v in f.items() if k != "Code"}
        if FunctionName == self.EDGE:
            return {"Configuration": conf, "Code": {"ImageUri": f["Code"]["ImageUri"]}}
        return {"Configuration": conf, "Code": {"RepositoryType": "S3", "Location": "https://example"}}

    def get_function_configuration(self, FunctionName):
        return {
            k: copy.deepcopy(v) for k, v in self._fn(FunctionName, "GetFunctionConfiguration").items() if k != "Code"
        }

    def create_function(self, **req):
        name = req["FunctionName"]
        arn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{name}"
        if name == self.EDGE:
            assert self.w.fn is None
            self.w.fn = copy.deepcopy(req)
            return {"FunctionName": name, "FunctionArn": arn}
        assert name not in self.w.fns
        code = req["Code"]["ZipFile"]
        f = {k: copy.deepcopy(v) for k, v in req.items() if k != "Code"}
        f.update(
            FunctionArn=arn,
            CodeSha256=base64.b64encode(__import__("hashlib").sha256(code).digest()).decode(),
            State="Active",
        )
        self.w.fns[name] = f
        return {"FunctionName": name, "FunctionArn": arn, "CodeSha256": f["CodeSha256"]}

    def update_function_code(self, FunctionName, ImageUri=None, ZipFile=None):
        f = self._fn(FunctionName, "UpdateFunctionCode")
        if FunctionName == self.EDGE:
            f["Code"] = {"ImageUri": ImageUri}
        else:
            f["CodeSha256"] = base64.b64encode(__import__("hashlib").sha256(ZipFile).digest()).decode()
        return {}

    def update_function_configuration(self, FunctionName, **kw):
        self._fn(FunctionName, "UpdateFunctionConfiguration").update(copy.deepcopy(kw))
        return {}

    def get_function_concurrency(self, FunctionName):
        self._fn(FunctionName, "GetFunctionConcurrency")
        c = self.w.fn_concurrency.get(FunctionName)
        return {"ReservedConcurrentExecutions": c} if c is not None else {}

    def put_function_concurrency(self, FunctionName, ReservedConcurrentExecutions):
        self._fn(FunctionName, "PutFunctionConcurrency")
        self.w.fn_concurrency[FunctionName] = ReservedConcurrentExecutions
        return {"ReservedConcurrentExecutions": ReservedConcurrentExecutions}

    def get_function_url_config(self, FunctionName):
        if not self.w.fn_url:
            raise error("ResourceNotFoundException", "GetFunctionUrlConfig")
        return dict(self.w.fn_url)

    def create_function_url_config(self, FunctionName, AuthType, InvokeMode):
        self.w.fn_url = {
            "FunctionUrl": "https://abcdefghij1234567890.lambda-url.us-east-1.on.aws/",
            "AuthType": AuthType,
            "InvokeMode": InvokeMode,
        }
        return dict(self.w.fn_url)

    def update_function_url_config(self, FunctionName, **kw):
        self.w.fn_url.update(kw)
        return dict(self.w.fn_url)

    def delete_function_url_config(self, FunctionName):
        self.w.fn_url = None
        return {}

    def delete_function(self, FunctionName):
        if FunctionName == self.EDGE:
            self.w.fn, self.w.fn_statements = None, []
        else:
            self._fn(FunctionName, "DeleteFunction")
            self.w.fns.pop(FunctionName)
            self.w.fn_policies.pop(FunctionName, None)
            self.w.fn_concurrency.pop(FunctionName, None)
        return {}

    def get_policy(self, FunctionName):
        statements = self._statements(FunctionName)
        if not statements:
            raise error("ResourceNotFoundException", "GetPolicy")
        return {"Policy": json.dumps({"Version": "2012-10-17", "Statement": statements})}

    def add_permission(self, FunctionName, StatementId, Action, Principal, SourceArn):
        statements = self._statements(FunctionName)
        assert StatementId not in {s["Sid"] for s in statements}
        statements.append(
            {
                "Sid": StatementId,
                "Effect": "Allow",
                "Principal": {"Service": Principal},
                "Action": Action,
                "Resource": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FunctionName}",
                "Condition": {"ArnLike": {"AWS:SourceArn": SourceArn}},
            }
        )
        return {"Statement": "{}"}

    def remove_permission(self, FunctionName, StatementId):
        statements = self._statements(FunctionName)
        statements[:] = [s for s in statements if s["Sid"] != StatementId]
        return {}


class DynamoDb(Svc):
    """One table at a time is enough here. Only the condition expressions devbox.py and provisioner.py use."""

    def _table(self, name, op):
        if name not in self.w.tables:
            raise error("ResourceNotFoundException", op, f"Requested resource not found: Table: {name} not found")
        return self.w.tables[name]

    def create_table(self, TableName, KeySchema, DeletionProtectionEnabled=False, **kw):
        assert TableName not in self.w.tables
        self.w.tables[TableName] = {
            "key": KeySchema[0]["AttributeName"],
            "items": {},
            "status": "CREATING",
            "protected": DeletionProtectionEnabled,
            "billing": kw.get("BillingMode"),
        }
        return {"TableDescription": {"TableName": TableName, "TableStatus": "CREATING"}}

    def describe_table(self, TableName):
        t = self._table(TableName, "DescribeTable")
        out = {"TableName": TableName, "TableStatus": t["status"], "DeletionProtectionEnabled": t["protected"]}
        t["status"] = "ACTIVE"
        return {"Table": out}

    def update_table(self, TableName, DeletionProtectionEnabled=None, **kw):
        t = self._table(TableName, "UpdateTable")
        if DeletionProtectionEnabled is not None:
            t["protected"] = DeletionProtectionEnabled
        return {"TableDescription": {"TableName": TableName}}

    def delete_table(self, TableName):
        t = self._table(TableName, "DeleteTable")
        if t["protected"]:
            raise error(
                "ValidationException",
                "DeleteTable",
                "Resource cannot be deleted as it is currently protected against deletion.",
            )
        del self.w.tables[TableName]
        return {"TableDescription": {"TableName": TableName}}

    def _k(self, t, Key):
        return Key[t["key"]]["S"]

    def get_item(self, TableName, Key, **kw):
        t = self._table(TableName, "GetItem")
        item = t["items"].get(self._k(t, Key))
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, TableName, Item, ConditionExpression=None, **kw):
        t = self._table(TableName, "PutItem")
        k = Item[t["key"]]["S"]
        if ConditionExpression:
            assert ConditionExpression == "attribute_not_exists(#k)", ConditionExpression
            if k in t["items"]:
                raise error("ConditionalCheckFailedException", "PutItem", "The conditional request failed")
        t["items"][k] = copy.deepcopy(Item)
        return {}

    def update_item(
        self, TableName, Key, UpdateExpression, ConditionExpression=None, ExpressionAttributeValues=None, **kw
    ):
        t = self._table(TableName, "UpdateItem")
        k, vals = self._k(t, Key), ExpressionAttributeValues or {}
        assert UpdateExpression == "SET lockUntil = :until", UpdateExpression
        item = t["items"].get(k)
        if ConditionExpression:
            held = item and "lockUntil" in item and float(item["lockUntil"]["N"]) >= float(vals[":now"]["N"])
            if not item or held:
                raise error("ConditionalCheckFailedException", "UpdateItem", "The conditional request failed")
        item = t["items"].setdefault(k, {t["key"]: {"S": k}})
        item["lockUntil"] = vals[":until"]
        return {}

    def scan(self, TableName, **kw):
        t = self._table(TableName, "Scan")
        return {"Items": [copy.deepcopy(i) for i in t["items"].values()], "Count": len(t["items"])}


class ApiGatewayV2(Svc):
    def _api(self, ApiId, op):
        if ApiId not in self.w.apis:
            raise error("NotFoundException", op, "Invalid API identifier specified")
        return self.w.apis[ApiId]

    def get_apis(self, **kw):
        return {"Items": [{k: v for k, v in a.items() if not k.startswith("_")} for a in self.w.apis.values()]}

    def create_api(self, Name, ProtocolType, **kw):
        aid = self.w.hex(10)[-10:]
        self.w.apis[aid] = {
            "ApiId": aid,
            "Name": Name,
            "ProtocolType": ProtocolType,
            "ApiEndpoint": f"https://{aid}.execute-api.{REGION}.amazonaws.com",
            "_authorizers": {},
            "_integrations": {},
            "_routes": {},
            "_stages": {},
        }
        return {k: v for k, v in self.w.apis[aid].items() if not k.startswith("_")}

    def delete_api(self, ApiId):
        self._api(ApiId, "DeleteApi")
        del self.w.apis[ApiId]
        return {}

    def _items(self, ApiId, kind, op):
        return {"Items": [copy.deepcopy(x) for x in self._api(ApiId, op)[kind].values()]}

    def get_authorizers(self, ApiId, **kw):
        return self._items(ApiId, "_authorizers", "GetAuthorizers")

    def create_authorizer(self, ApiId, **req):
        aid = "au" + self.w.hex(5)
        self._api(ApiId, "CreateAuthorizer")["_authorizers"][aid] = {"AuthorizerId": aid, **copy.deepcopy(req)}
        return {"AuthorizerId": aid, **req}

    def update_authorizer(self, ApiId, AuthorizerId, **req):
        self._api(ApiId, "UpdateAuthorizer")["_authorizers"][AuthorizerId].update(copy.deepcopy(req))
        return {"AuthorizerId": AuthorizerId}

    def get_integrations(self, ApiId, **kw):
        return self._items(ApiId, "_integrations", "GetIntegrations")

    def create_integration(self, ApiId, **req):
        iid = "in" + self.w.hex(5)
        self._api(ApiId, "CreateIntegration")["_integrations"][iid] = {"IntegrationId": iid, **copy.deepcopy(req)}
        return {"IntegrationId": iid}

    def get_routes(self, ApiId, **kw):
        return self._items(ApiId, "_routes", "GetRoutes")

    def create_route(self, ApiId, **req):
        rid = "ro" + self.w.hex(5)
        self._api(ApiId, "CreateRoute")["_routes"][rid] = {"RouteId": rid, **copy.deepcopy(req)}
        return {"RouteId": rid}

    def update_route(self, ApiId, RouteId, **req):
        self._api(ApiId, "UpdateRoute")["_routes"][RouteId].update(copy.deepcopy(req))
        return {"RouteId": RouteId}

    def get_stages(self, ApiId, **kw):
        return self._items(ApiId, "_stages", "GetStages")

    def create_stage(self, ApiId, StageName, **req):
        self._api(ApiId, "CreateStage")["_stages"][StageName] = {"StageName": StageName, **copy.deepcopy(req)}
        return {"StageName": StageName}

    def update_stage(self, ApiId, StageName, **req):
        self._api(ApiId, "UpdateStage")["_stages"][StageName].update(copy.deepcopy(req))
        return {"StageName": StageName}


class CloudFront(Svc):
    def list_origin_access_controls(self, **kw):
        items = [
            {
                "Id": i,
                "Name": o["Name"],
                "Description": "",
                "SigningProtocol": "sigv4",
                "SigningBehavior": "always",
                "OriginAccessControlOriginType": "lambda",
            }
            for i, o in self.w.oacs.items()
        ]
        return {
            "OriginAccessControlList": {
                "Marker": "",
                "MaxItems": 100,
                "IsTruncated": False,
                "Quantity": len(items),
                "Items": items,
            }
        }

    def create_origin_access_control(self, OriginAccessControlConfig):
        oid = "E" + self.w.hex(13).upper()
        self.w.oacs[oid] = dict(OriginAccessControlConfig)
        return {
            "OriginAccessControl": {"Id": oid, "OriginAccessControlConfig": OriginAccessControlConfig},
            "ETag": "E1",
        }

    def get_origin_access_control(self, Id):
        return {"OriginAccessControl": {"Id": Id, "OriginAccessControlConfig": self.w.oacs[Id]}, "ETag": "E1"}

    def delete_origin_access_control(self, Id, IfMatch):
        assert not [
            d for d in self.w.dists.values() if d["config"]["Origins"]["Items"][0].get("OriginAccessControlId") == Id
        ], "OAC in use"
        del self.w.oacs[Id]
        return {}

    def _summary(self, did, d):
        return {
            "Id": did,
            "ARN": f"arn:aws:cloudfront::{ACCOUNT}:distribution/{did}",
            "DomainName": d["domain"],
            "Status": d["status"],
            "Comment": d["config"]["Comment"],
            "Enabled": d["config"]["Enabled"],
        }

    def list_distributions(self, **kw):
        items = [self._summary(i, d) for i, d in self.w.dists.items()]
        return {
            "DistributionList": {
                "Marker": "",
                "MaxItems": 100,
                "IsTruncated": False,
                "Quantity": len(items),
                "Items": items,
            }
        }

    def create_distribution_with_tags(self, DistributionConfigWithTags):
        did = "E" + self.w.hex(13).upper()
        cfg = copy.deepcopy(DistributionConfigWithTags["DistributionConfig"])
        self.w.dists[did] = {
            "config": cfg,
            "etag": "E1",
            "domain": f"d{self.w.hex(13)}.cloudfront.net",
            "status": "InProgress",
        }
        return {"Distribution": {**self._summary(did, self.w.dists[did]), "DistributionConfig": cfg}, "ETag": "E1"}

    def get_distribution_config(self, Id):
        d = self.w.dists[Id]
        return {"DistributionConfig": copy.deepcopy(d["config"]), "ETag": d["etag"]}

    def get_distribution(self, Id):
        d = self.w.dists[Id]
        d["status"] = "Deployed"
        return {
            "Distribution": {**self._summary(Id, d), "DistributionConfig": copy.deepcopy(d["config"])},
            "ETag": d["etag"],
        }

    def update_distribution(self, Id, IfMatch, DistributionConfig):
        d = self.w.dists[Id]
        assert IfMatch == d["etag"]
        d["config"], d["etag"], d["status"] = (
            copy.deepcopy(DistributionConfig),
            f"E{int(d['etag'][1:]) + 1}",
            "InProgress",
        )
        return {"Distribution": self._summary(Id, d), "ETag": d["etag"]}

    def delete_distribution(self, Id, IfMatch):
        d = self.w.dists[Id]
        assert IfMatch == d["etag"] and not d["config"]["Enabled"], "delete a distribution only once it's disabled"
        del self.w.dists[Id]
        return {}


class AgentCoreControl(Svc):
    def _arn(self, kind, rid):
        return f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:{kind}/{rid}"

    @staticmethod
    def _ready_after_one(obj, key="status", ready="READY"):
        """A new resource is CREATING (or UPDATING) on the first read, then ready."""
        if obj.get("_reads", 0) >= 1 and obj[key] in ("CREATING", "UPDATING"):
            obj[key] = ready
        obj["_reads"] = obj.get("_reads", 0) + 1

    @staticmethod
    def _public(obj):
        return {k: copy.deepcopy(v) for k, v in obj.items() if not k.startswith("_")}

    # gateways
    def list_gateways(self, **kw):
        return {
            "items": [
                {"gatewayId": g["gatewayId"], "name": g["name"], "status": g["status"]}
                for g in self.w.gateways.values()
            ]
        }

    def create_gateway(self, name, roleArn, protocolType, authorizerType, **kw):
        gid = f"{name}-{self.w.suffix()}"
        self.w.gateways[gid] = {
            "gatewayId": gid,
            "gatewayArn": self._arn("gateway", gid),
            "name": name,
            "status": "CREATING",
            "gatewayUrl": f"https://{gid}.gateway.bedrock-agentcore.{REGION}.amazonaws.com/mcp",
            "roleArn": roleArn,
            "authorizerType": authorizerType,
        }
        return self._public(self.w.gateways[gid])

    def get_gateway(self, gatewayIdentifier):
        g = self.w.gateways.get(gatewayIdentifier)
        if not g:
            raise error("ResourceNotFoundException", "GetGateway")
        self._ready_after_one(g)
        return self._public(g)

    def update_gateway(self, gatewayIdentifier, **kw):
        g = self.w.gateways[gatewayIdentifier]
        g.update(copy.deepcopy(kw))
        g["status"], g["_reads"] = "UPDATING", 0
        return self._public(g)

    def delete_gateway(self, gatewayIdentifier):
        assert not [t for t in self.w.targets.values() if t["gw"] == gatewayIdentifier], "targets left"
        del self.w.gateways[gatewayIdentifier]
        return {"gatewayId": gatewayIdentifier, "status": "DELETING"}

    def list_gateway_targets(self, gatewayIdentifier, **kw):
        return {
            "items": [
                {"targetId": t["targetId"], "name": t["name"], "status": t["status"]}
                for t in self.w.targets.values()
                if t["gw"] == gatewayIdentifier
            ]
        }

    def create_gateway_target(self, gatewayIdentifier, name, **kw):
        tid = self.w.hex(10).upper()
        self.w.targets[tid] = {"targetId": tid, "name": name, "gw": gatewayIdentifier, "status": "CREATING"}
        return {"targetId": tid, "status": "CREATING"}

    def get_gateway_target(self, gatewayIdentifier, targetId):
        t = self.w.targets.get(targetId)
        if not t:
            raise error("ResourceNotFoundException", "GetGatewayTarget")
        self._ready_after_one(t)
        return {
            "targetId": targetId,
            "status": t["status"],
            **({"statusReasons": t["statusReasons"]} if t.get("statusReasons") else {}),
        }

    def delete_gateway_target(self, gatewayIdentifier, targetId):
        del self.w.targets[targetId]
        return {}

    # policy engines and Cedar
    def list_policy_engines(self, **kw):
        return {"policyEngines": [self._public(e) for e in self.w.engines.values()]}

    def create_policy_engine(self, name, **kw):
        eid = f"{name}-{self.w.suffix()}"
        self.w.engines[eid] = {
            "policyEngineId": eid,
            "name": name,
            "policyEngineArn": self._arn("policy-engine", eid),
            "status": "CREATING",
        }
        return self._public(self.w.engines[eid])

    def get_policy_engine(self, policyEngineId):
        e = self.w.engines[policyEngineId]
        self._ready_after_one(e, ready="ACTIVE")
        return self._public(e)

    def delete_policy_engine(self, policyEngineId):
        assert not [p for p in self.w.policies.values() if p["policyEngineId"] == policyEngineId], "Cedar rules left"
        del self.w.engines[policyEngineId]
        return {}

    def list_policies(self, policyEngineId, **kw):
        return {
            "policies": [self._public(p) for p in self.w.policies.values() if p["policyEngineId"] == policyEngineId]
        }

    def create_policy(self, policyEngineId, name, definition, **kw):
        pid = f"{name}-{self.w.suffix()}"
        self.w.policies[pid] = {
            "policyId": pid,
            "policyEngineId": policyEngineId,
            "name": name,
            "definition": copy.deepcopy(definition),
            "status": "CREATING",
        }
        return self._public(self.w.policies[pid])

    def get_policy(self, policyEngineId, policyId):
        p = self.w.policies.get(policyId)
        if not p:
            raise error("ResourceNotFoundException", "GetPolicy")
        self._ready_after_one(p, ready="ACTIVE")
        return self._public(p)

    def update_policy(self, policyEngineId, policyId, definition, **kw):
        p = self.w.policies[policyId]
        p["definition"], p["status"], p["_reads"] = copy.deepcopy(definition), "UPDATING", 0
        return self._public(p)

    def delete_policy(self, policyEngineId, policyId):
        del self.w.policies[policyId]
        return {}

    # resource policies
    def get_resource_policy(self, resourceArn):
        if resourceArn not in self.w.rbps:
            raise error("ResourceNotFoundException", "GetResourcePolicy")
        return {"policy": self.w.rbps[resourceArn]}

    def put_resource_policy(self, resourceArn, policy):
        self.w.rbps[resourceArn] = policy
        return {"policy": policy}

    # capacity providers
    def list_capacity_providers(self, **kw):
        return {
            "capacityProviders": [
                {k: c[k] for k in ("capacityProviderId", "capacityProviderArn", "name", "status")}
                for c in self.w.cps.values()
            ]
        }

    def create_capacity_provider(self, name, **req):
        assert name not in {c["name"] for c in self.w.cps.values()}, "a capacity provider with that name exists"
        cid = f"{name}-{self.w.suffix()}"
        self.w.cps[cid] = {
            "capacityProviderId": cid,
            "capacityProviderArn": self._arn("capacity-provider", cid),
            "name": name,
            "status": "CREATING",
            **copy.deepcopy(req),
        }
        return {
            "capacityProviderId": cid,
            "capacityProviderArn": self.w.cps[cid]["capacityProviderArn"],
            "status": "CREATING",
        }

    def get_capacity_provider(self, capacityProviderId):
        c = self.w.cps.get(capacityProviderId)
        if not c:
            raise error("ResourceNotFoundException", "GetCapacityProvider")
        if c["status"] == "DELETING":
            if self.w.cp_delete_failures:
                self.w.cp_delete_failures -= 1
                c["status"] = "DELETE_FAILED"
            else:
                del self.w.cps[capacityProviderId]
                raise error("ResourceNotFoundException", "GetCapacityProvider")
        self._ready_after_one(c)
        return self._public(c)

    def delete_capacity_provider(self, capacityProviderId):
        assert not [
            r
            for r in self.w.runtimes.values()
            if (r.get("capacityProviderConfiguration") or {}).get("capacityProviderArn")
            == self.w.cps[capacityProviderId]["capacityProviderArn"]
        ], "runtimes still use the capacity provider"
        self.w.cps[capacityProviderId]["status"] = "DELETING"
        return {"capacityProviderId": capacityProviderId, "status": "DELETING"}

    def list_agent_runtime_versions_by_capacity_provider(self, capacityProviderId, **kw):
        arn = self.w.cps[capacityProviderId]["capacityProviderArn"]
        return {
            "agentRuntimes": [
                {
                    "agentRuntimeArn": r["agentRuntimeArn"],
                    "agentRuntimeVersion": r["agentRuntimeVersion"],
                    "status": r["status"],
                }
                for r in self.w.runtimes.values()
                if (r.get("capacityProviderConfiguration") or {}).get("capacityProviderArn") == arn
            ]
        }

    # runtimes
    def list_agent_runtimes(self, **kw):
        return {
            "agentRuntimes": [
                {
                    "agentRuntimeArn": r["agentRuntimeArn"],
                    "agentRuntimeId": r["agentRuntimeId"],
                    "agentRuntimeName": r["agentRuntimeName"],
                    "agentRuntimeVersion": r["agentRuntimeVersion"],
                    "description": r.get("description", "-"),
                    "status": r["status"],
                }
                for r in self.w.runtimes.values()
            ]
        }

    def _check_runtime(self, req, op):
        """What AgentCore itself refuses (docs: runtime-filesystem-configurations.html): EFS, S3 Files and session storage
        only on microVM (never beside a capacity provider), EFS only in VPC mode, and the ids have to exist."""
        fs = req.get("filesystemConfigurations") or []
        on_cp = bool(req.get("capacityProviderConfiguration"))
        if on_cp and any(set(f) - {"capacityProviderVolume"} for f in fs):
            raise error(
                "ValidationException",
                op,
                "sessionStorage, s3FilesAccessPoint and efsAccessPoint aren't supported with a capacity provider",
            )
        if not on_cp and any("capacityProviderVolume" in f for f in fs):
            raise error("ValidationException", op, "capacityProviderVolume needs a capacity provider")
        net = req.get("networkConfiguration") or {}
        if any("efsAccessPoint" in f for f in fs) and net.get("networkMode") != "VPC":
            raise error("ValidationException", op, "an EFS access point needs networkMode VPC")
        for f in fs:
            if "efsAccessPoint" in f:
                arn = f["efsAccessPoint"]["accessPointArn"]
                assert any(a["AccessPointArn"] == arn for a in self.w.efs_aps.values()), f"no access point {arn}"
                # live 2026-09-29: not in the doc, but AgentCore checks these two on the execution role
                role = self.w.roles.get(req.get("roleArn", "").rsplit("/", 1)[-1]) or {}
                granted = {
                    a
                    for doc in role.get("inline", {}).values()
                    for s in doc["Statement"]
                    if s["Effect"] == "Allow"
                    for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])
                }
                if not {"elasticfilesystem:DescribeAccessPoints", "elasticfilesystem:DescribeMountTargets"} <= granted:
                    raise error(
                        "ValidationException",
                        op,
                        "Execution role is missing required filesystem permissions. Ensure the "
                        "role has elasticfilesystem:DescribeAccessPoints and elasticfilesystem:DescribeMountTargets",
                    )
        cfg = net.get("networkModeConfig") or {}
        assert all(s in self.w.subnets for s in cfg.get("subnets", [])), "unknown subnet"
        assert all(g in self.w.sgs for g in cfg.get("securityGroups", [])), "unknown security group"

    def create_agent_runtime(self, agentRuntimeName, **req):
        self._check_runtime(req, "CreateAgentRuntime")
        rid = f"{agentRuntimeName}-{self.w.suffix()}"
        self.w.runtimes[rid] = {
            "agentRuntimeId": rid,
            "agentRuntimeArn": self._arn("runtime", rid),
            "agentRuntimeName": agentRuntimeName,
            "agentRuntimeVersion": "1",
            "status": "CREATING",
            **copy.deepcopy({k: v for k, v in req.items() if k != "tags"}),
        }
        return {
            k: self.w.runtimes[rid][k] for k in ("agentRuntimeId", "agentRuntimeArn", "agentRuntimeVersion", "status")
        }

    def get_agent_runtime(self, agentRuntimeId):
        r = self.w.runtimes.get(agentRuntimeId)
        if not r:
            raise error("ResourceNotFoundException", "GetAgentRuntime")
        self._ready_after_one(r)
        out = self._public(r)
        vpc = (out.get("networkConfiguration") or {}).get("networkModeConfig")
        if vpc is not None:
            vpc.setdefault("requireServiceS3Endpoint", False)  # the real service adds it to what it shows
        return out

    def update_agent_runtime(self, agentRuntimeId, **req):
        r = self.w.runtimes[agentRuntimeId]
        if bool(r.get("capacityProviderConfiguration")) != bool(req.get("capacityProviderConfiguration")):
            raise error("ValidationException", "UpdateAgentRuntime", "a runtime's compute type can't change")
        if "requireServiceS3Endpoint" in ((req.get("networkConfiguration") or {}).get("networkModeConfig") or {}):
            # live 2026-09-29: GetAgentRuntime shows it, but it can't be sent back
            raise error(
                "ValidationException",
                "UpdateAgentRuntime",
                "Agents created after 2026-06-11T00:00:00Z cannot modify requireServiceS3Endpoint.",
            )
        self._check_runtime(req, "UpdateAgentRuntime")
        keep = {k: r[k] for k in ("agentRuntimeId", "agentRuntimeArn", "agentRuntimeName")}
        version = str(int(r["agentRuntimeVersion"]) + 1)
        r.clear()  # the whole configuration is replaced
        r.update(keep, agentRuntimeVersion=version, status="UPDATING", **copy.deepcopy(req))
        return {
            "agentRuntimeId": agentRuntimeId,
            "agentRuntimeArn": keep["agentRuntimeArn"],
            "agentRuntimeVersion": version,
            "status": "UPDATING",
        }

    def delete_agent_runtime(self, agentRuntimeId):
        del self.w.runtimes[agentRuntimeId]
        return {"status": "DELETING", "agentRuntimeId": agentRuntimeId}

    def update_capacity_provider(self, **kw):
        raise AssertionError("capacity providers are write-once: UpdateCapacityProvider must never be called")


class AgentCoreData(Svc):
    def delete_capacity_provider_session(self, capacityProviderId, sessionId):
        key = (capacityProviderId, sessionId)
        if key in self.w.deleted_sessions:
            return {
                "capacityProviderArn": self.w.cps[capacityProviderId]["capacityProviderArn"],
                "sessionId": sessionId,
                "status": "Deleted",
            }
        self.w.deleted_sessions.append(key)
        return {
            "capacityProviderArn": self.w.cps[capacityProviderId]["capacityProviderArn"],
            "sessionId": sessionId,
            "status": "Deleting",
        }


class Efs(Svc):
    """EFS: file systems, mount targets (and their security groups), access points and file system policies. Something
    new is `creating` on the first read, then `available`; a deleted mount target is gone on the next read."""

    NOW = datetime.datetime(2026, 9, 29, tzinfo=datetime.timezone.utc)

    def _fs(self, fid, op="DescribeFileSystems"):
        if fid not in self.w.efs_fs:
            raise error("FileSystemNotFound", op, f"File system '{fid}' does not exist.", 404)
        return self.w.efs_fs[fid]

    @staticmethod
    def _ready(obj):
        if obj.get("_reads", 0) >= 1 and obj["LifeCycleState"] == "creating":
            obj["LifeCycleState"] = "available"
        obj["_reads"] = obj.get("_reads", 0) + 1

    # file systems
    def create_file_system(self, CreationToken, **kw):
        if [f for f in self.w.efs_fs.values() if f["CreationToken"] == CreationToken]:
            raise error(
                "FileSystemAlreadyExists", "CreateFileSystem", "a file system with that creation token exists", 409
            )
        fid = f"fs-{self.w.hex()}"
        tags = kw.get("Tags", [])
        self.w.efs_fs[fid] = {
            "OwnerId": ACCOUNT,
            "CreationToken": CreationToken,
            "FileSystemId": fid,
            "FileSystemArn": f"arn:aws:elasticfilesystem:{REGION}:{ACCOUNT}:file-system/{fid}",
            "CreationTime": self.NOW,
            "LifeCycleState": "creating",
            "NumberOfMountTargets": 0,
            "SizeInBytes": {"Value": 6144},
            "PerformanceMode": kw.get("PerformanceMode", "generalPurpose"),
            "Encrypted": kw.get("Encrypted", False),
            "ThroughputMode": kw.get("ThroughputMode", "bursting"),
            "Tags": tags,
            **name_field(tags),
        }
        return Svc_public(self.w.efs_fs[fid])

    def describe_file_systems(self, CreationToken=None, FileSystemId=None, **kw):
        if FileSystemId:
            self._fs(FileSystemId)
        out = []
        for f in self.w.efs_fs.values():
            if (CreationToken in (None, f["CreationToken"])) and (FileSystemId in (None, f["FileSystemId"])):
                pub = Svc_public(f)
                self._ready(f)
                out.append(pub)
        return {"FileSystems": out}

    def delete_file_system(self, FileSystemId):
        self._fs(FileSystemId, "DeleteFileSystem")
        if [m for m in self.w.efs_mts.values() if m["FileSystemId"] == FileSystemId]:
            raise error("FileSystemInUse", "DeleteFileSystem", "the file system has mount targets", 409)
        assert not [a for a in self.w.efs_aps.values() if a["FileSystemId"] == FileSystemId], (
            "access points left on the file system"
        )
        del self.w.efs_fs[FileSystemId]
        self.w.efs_policies.pop(FileSystemId, None)
        return {}

    # mount targets
    def create_mount_target(self, FileSystemId, SubnetId, SecurityGroups=None, **kw):
        fs = self._fs(FileSystemId, "CreateMountTarget")
        if fs["LifeCycleState"] != "available":
            raise error(
                "IncorrectFileSystemLifeCycleState", "CreateMountTarget", "the file system isn't available yet", 409
            )
        sn = self.w.subnets[SubnetId]
        mine = [m for m in self.w.efs_mts.values() if m["FileSystemId"] == FileSystemId]
        assert not [m for m in mine if m["VpcId"] != sn["VpcId"]], "a file system's mount targets live in one VPC"
        if [m for m in mine if m["AvailabilityZoneName"] == sn["AvailabilityZone"]]:
            raise error(
                "MountTargetConflict", "CreateMountTarget", "a mount target exists in that Availability Zone", 409
            )
        assert SecurityGroups and all(g in self.w.sgs and self.w.sgs[g]["VpcId"] == sn["VpcId"] for g in SecurityGroups)
        mid = f"fsmt-{self.w.hex()}"
        self.w.efs_mts[mid] = {
            "OwnerId": ACCOUNT,
            "MountTargetId": mid,
            "FileSystemId": FileSystemId,
            "SubnetId": SubnetId,
            "VpcId": sn["VpcId"],
            "LifeCycleState": "creating",
            "IpAddress": "10.40.1.200",
            "NetworkInterfaceId": f"eni-{self.w.hex()}",
            "AvailabilityZoneName": sn["AvailabilityZone"],
            "AvailabilityZoneId": self.w.az_ids[sn["AvailabilityZone"]],
        }
        self.w.efs_mt_sgs[mid] = list(SecurityGroups)
        fs["NumberOfMountTargets"] += 1
        return Svc_public(self.w.efs_mts[mid])

    def describe_mount_targets(self, FileSystemId=None, MountTargetId=None, **kw):
        if MountTargetId and MountTargetId not in self.w.efs_mts:
            raise error("MountTargetNotFound", "DescribeMountTargets", "no such mount target", 404)
        if FileSystemId:
            self._fs(FileSystemId, "DescribeMountTargets")
        out = []
        for mid, m in list(self.w.efs_mts.items()):
            if (FileSystemId in (None, m["FileSystemId"])) and (MountTargetId in (None, mid)):
                out.append(Svc_public(m))
                if m["LifeCycleState"] == "deleting":
                    # like EFS: a deleted mount target stays "deleting" for a while (here, two reads), and the file system
                    # can't be deleted until it's gone (live 2026-10-02: FileSystemInUse right after DeleteMountTarget)
                    m["_deleting_reads"] = m.get("_deleting_reads", 0) + 1
                    if m["_deleting_reads"] >= 2:
                        del self.w.efs_mts[mid]
                        self.w.efs_fs[m["FileSystemId"]]["NumberOfMountTargets"] -= 1
                else:
                    self._ready(m)
        return {"MountTargets": out}

    def describe_mount_target_security_groups(self, MountTargetId):
        return {"SecurityGroups": list(self.w.efs_mt_sgs[MountTargetId])}

    def modify_mount_target_security_groups(self, MountTargetId, SecurityGroups):
        self.w.efs_mt_sgs[MountTargetId] = list(SecurityGroups)
        return {}

    def delete_mount_target(self, MountTargetId):
        self.w.efs_mts[MountTargetId]["LifeCycleState"] = "deleting"
        return {}

    # access points
    def create_access_point(self, ClientToken, FileSystemId, **kw):
        self._fs(FileSystemId, "CreateAccessPoint")
        aid = f"fsap-{self.w.hex()}"
        tags = kw.get("Tags", [])
        self.w.efs_aps[aid] = {
            "ClientToken": ClientToken,
            "AccessPointId": aid,
            "FileSystemId": FileSystemId,
            "OwnerId": ACCOUNT,
            "AccessPointArn": f"arn:aws:elasticfilesystem:{REGION}:{ACCOUNT}:access-point/{aid}",
            "PosixUser": copy.deepcopy(kw.get("PosixUser")),
            "RootDirectory": copy.deepcopy(kw.get("RootDirectory", {"Path": "/"})),
            "Tags": tags,
            "LifeCycleState": "creating",
            **name_field(tags),
        }
        return Svc_public(self.w.efs_aps[aid])

    def describe_access_points(self, FileSystemId=None, AccessPointId=None, **kw):
        if AccessPointId and AccessPointId not in self.w.efs_aps:
            raise error("AccessPointNotFound", "DescribeAccessPoints", "no such access point", 404)
        out = []
        for aid, a in self.w.efs_aps.items():
            if (FileSystemId in (None, a["FileSystemId"])) and (AccessPointId in (None, aid)):
                out.append(Svc_public(a))
                self._ready(a)
        return {"AccessPoints": out}

    def delete_access_point(self, AccessPointId):
        del self.w.efs_aps[AccessPointId]
        return {}

    # file system policy
    def put_file_system_policy(self, FileSystemId, Policy, **kw):
        self._fs(FileSystemId, "PutFileSystemPolicy")
        doc = json.loads(Policy)
        for st in doc["Statement"]:
            principal = (
                (st.get("Principal") or {}).get("AWS") if isinstance(st.get("Principal"), dict) else st.get("Principal")
            )
            for p in [principal] if isinstance(principal, str) else principal or []:
                if p != "*" and p.rsplit("/", 1)[-1] not in self.w.roles:
                    raise error(
                        "InvalidPolicyException", "PutFileSystemPolicy", f"Policy contains invalid Principal block: {p}"
                    )
        self.w.efs_policies[FileSystemId] = Policy
        return {"FileSystemId": FileSystemId, "Policy": Policy}

    def describe_file_system_policy(self, FileSystemId):
        self._fs(FileSystemId, "DescribeFileSystemPolicy")
        if FileSystemId not in self.w.efs_policies:
            raise error("PolicyNotFound", "DescribeFileSystemPolicy", "No policy is set", 404)
        return {"FileSystemId": FileSystemId, "Policy": self.w.efs_policies[FileSystemId]}


def Svc_public(obj):
    return {k: copy.deepcopy(v) for k, v in obj.items() if not k.startswith("_")}
