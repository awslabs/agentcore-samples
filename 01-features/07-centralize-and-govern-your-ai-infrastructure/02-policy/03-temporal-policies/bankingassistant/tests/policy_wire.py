"""In-memory policy service using the real pinned serializer AND response parser.

No boto3 client, credentials or network. Responses are explicit service fields,
not request echoes. Valid synthetic IDs are intentionally longer than model minima.
"""
import copy
import json
import os
from types import SimpleNamespace
from botocore.exceptions import ClientError
from botocore.parsers import create_parser
from botocore.serialize import create_serializer
from botocore.validate import validate_parameters
from policy_contract import require_policy_model

ENGINE = "banking_policy_engine-abcdefghij"
POLICY = "permit_balance-abcdefghij"


class PolicyWire:
    def __init__(self):
        self.model = require_policy_model()
        self.meta = SimpleNamespace(service_model=self.model)
        self.serializer = create_serializer(self.model.protocol)
        self.parser = create_parser(self.model.protocol)
        self.policies = {}
        self.requests = []
        self.responses = []
        self.get_transform = lambda body: body
        self.create_failure = None
        self.lose_create_response = False

    def request(self, operation, params):
        op = self.model.operation_model(operation)
        wire = self.serializer.serialize_to_request(params, op)
        self.requests.append((operation, copy.deepcopy(params), wire))

    def response(self, operation, body):
        op = self.model.operation_model(operation)
        validate_parameters(body, op.output_shape)
        raw = json.dumps(body).encode()
        parsed = self.parser.parse({"headers": {}, "status_code": 200, "body": raw}, op.output_shape)
        validate_parameters({k: v for k, v in parsed.items() if k != "ResponseMetadata"}, op.output_shape)
        self.responses.append((operation, raw, parsed))
        return parsed

    def not_found(self, operation):
        parsed = self.parser.parse({"headers": {"x-amzn-errortype": "ResourceNotFoundException"},
            "status_code": 404, "body": b'{"message":"synthetic policy absent"}'},
            self.model.operation_model(operation).output_shape)
        raise ClientError(parsed, operation)

    def create_policy(self, **p):
        self.request("CreatePolicy", p)
        if self.create_failure:
            raise self.create_failure
        policy_id = p["name"] + "-abcdefghij"
        self.policies[policy_id] = {"policyId": policy_id, "name": p["name"],
            "policyEngineId": p["policyEngineId"], "createdAt": 0, "updatedAt": 0,
            "policyArn": f"arn:aws:bedrock-agentcore:{os.environ.get('REGION', 'us-east-1')}:123456789012:policy-engine/{p['policyEngineId']}/policy/{policy_id}",
            "status": "ACTIVE", "enforcementMode": p.get("enforcementMode", "ACTIVE"),
            "definition": copy.deepcopy(p["definition"]), "description": p.get("description", "synthetic"),
            "statusReasons": []}
        result = self.response("CreatePolicy", {**self.policies[policy_id], "status": "CREATING"})
        if self.lose_create_response:
            raise TimeoutError("synthetic accepted create response lost")
        return result

    def get_policy(self, **p):
        self.request("GetPolicy", p)
        if p["policyId"] not in self.policies:
            self.not_found("GetPolicy")
        body = copy.deepcopy(self.policies[p["policyId"]])
        return self.response("GetPolicy", self.get_transform(body))

    def list_policies(self, **p):
        self.request("ListPolicies", p)
        members = self.model.operation_model("ListPolicies").output_shape.members["policies"].member.members
        items = [{k: v for k, v in body.items() if k in members} for body in self.policies.values()]
        return self.response("ListPolicies", {"policies": items})

    def delete_policy(self, **p):
        self.request("DeletePolicy", p)
        body = self.policies.pop(p["policyId"], None)
        if body is None:
            self.not_found("DeletePolicy")
        return self.response("DeletePolicy", {**body, "status": "DELETING"})
