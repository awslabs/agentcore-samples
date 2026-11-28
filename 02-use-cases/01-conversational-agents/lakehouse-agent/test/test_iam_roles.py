#!/usr/bin/env python3
"""
Unit test for the IAM role ownership helper (utils/iam_roles.py).

Runs fully offline (no AWS calls) using small in-memory fakes for the IAM, SSM
and Lake Formation clients, so it can be executed in CI or on a laptop without
credentials:

    python test/test_iam_roles.py

Covers:
  - normal path: create records the RoleId in SSM; delete removes the role and
    clears the record
  - re-run on this sample's own role: the role is re-used, nothing new is created
  - name collision: a same-name role this sample did not create is refused, with
    no IAM write
  - replacement: a role deleted and re-created under the same name is not deleted
  - unexpected attachments: an extra inline policy, managed policy or instance
    profile stops the delete before anything is removed
  - records gone: a role with no ownership record is left in place
  - Lake Formation: a registration is reported as not registered, ours or foreign

Exit code 0 when every check passes, 1 otherwise.
"""

import contextlib
import io
import sys
from pathlib import Path

# Make the project root importable so `utils` resolves when run from anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))

import boto3
import botocore.client


class NoAWSError(Exception):
    """Raised if anything in this test tries to reach AWS."""


def _no_aws(*args, **kwargs):
    raise NoAWSError("this test must not call AWS")


# No-AWS guard: any real boto3 or botocore call raises instead of reaching AWS.
boto3.client = _no_aws
boto3.Session = _no_aws
botocore.client.BaseClient._make_api_call = _no_aws

from utils.iam_roles import (
    FAILED,
    FOREIGN,
    LEFT,
    NOT_REGISTERED,
    OK,
    OURS,
    RoleOwnershipError,
    delete_owned_role,
    ensure_role,
    lf_registration_owner,
    record_name,
)

ROLE = "lakehouse-test-role"
TRUST = {"Version": "2012-10-17", "Statement": []}
OUR_INLINE = {"OurPolicy"}
OUR_MANAGED = {"arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"}
LF_ROLE = "LakeFormationS3TablesDataAccessRole"
LF_RESOURCE = "arn:aws:s3tables:us-east-1:111122223333:bucket/*"

# IAM calls that change something. A refusal must make none of them.
IAM_WRITES = {
    "create_role",
    "update_assume_role_policy",
    "put_role_policy",
    "attach_role_policy",
    "delete_role_policy",
    "detach_role_policy",
    "delete_role",
}


class _Exceptions:
    """Mimics the exception classes boto3 clients expose as client.exceptions.*."""

    class EntityAlreadyExistsException(Exception):
        pass

    class NoSuchEntityException(Exception):
        pass

    class ParameterNotFound(Exception):
        pass

    class EntityNotFoundException(Exception):
        pass


class FakeIAM:
    """Minimal in-memory stand-in for a boto3 IAM client (no AWS)."""

    def __init__(self):
        self.exceptions = _Exceptions
        self.roles = {}
        self.calls = []
        self._ids = 0

    def add_role(self, name, inline=(), managed=(), profiles=()):
        """Create a role out of band, as someone else would. Returns its RoleId."""
        self._ids += 1
        role_id = f"AROAFAKE{self._ids:04d}"
        self.roles[name] = {
            "RoleId": role_id,
            "inline": set(inline),
            "managed": set(managed),
            "profiles": set(profiles),
        }
        return role_id

    def writes(self):
        return [call for call in self.calls if call[0] in IAM_WRITES]

    def _role(self, name):
        if name not in self.roles:
            raise _Exceptions.NoSuchEntityException(name)
        return self.roles[name]

    def _view(self, name):
        role = self._role(name)
        return {"RoleName": name, "RoleId": role["RoleId"], "Arn": f"arn:aws:iam::111122223333:role/{name}"}

    def create_role(self, RoleName, **kwargs):
        if RoleName in self.roles:
            raise _Exceptions.EntityAlreadyExistsException(RoleName)
        self.add_role(RoleName)
        self.calls.append(("create_role", RoleName))
        return {"Role": self._view(RoleName)}

    def get_role(self, RoleName):
        return {"Role": self._view(RoleName)}

    def update_assume_role_policy(self, RoleName, PolicyDocument):
        self._role(RoleName)
        self.calls.append(("update_assume_role_policy", RoleName))

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        self._role(RoleName)["inline"].add(PolicyName)
        self.calls.append(("put_role_policy", RoleName, PolicyName))

    def attach_role_policy(self, RoleName, PolicyArn):
        self._role(RoleName)["managed"].add(PolicyArn)
        self.calls.append(("attach_role_policy", RoleName, PolicyArn))

    def list_role_policies(self, RoleName, **kwargs):
        return {"PolicyNames": sorted(self._role(RoleName)["inline"]), "IsTruncated": False}

    def list_attached_role_policies(self, RoleName, **kwargs):
        arns = sorted(self._role(RoleName)["managed"])
        return {"AttachedPolicies": [{"PolicyArn": arn} for arn in arns], "IsTruncated": False}

    def list_instance_profiles_for_role(self, RoleName, **kwargs):
        names = sorted(self._role(RoleName)["profiles"])
        return {"InstanceProfiles": [{"InstanceProfileName": n} for n in names], "IsTruncated": False}

    def delete_role_policy(self, RoleName, PolicyName):
        self._role(RoleName)["inline"].discard(PolicyName)
        self.calls.append(("delete_role_policy", RoleName, PolicyName))

    def detach_role_policy(self, RoleName, PolicyArn):
        self._role(RoleName)["managed"].discard(PolicyArn)
        self.calls.append(("detach_role_policy", RoleName, PolicyArn))

    def delete_role(self, RoleName):
        role = self._role(RoleName)
        self.calls.append(("delete_role", RoleName))
        # Like IAM: a role that still has attachments cannot be deleted.
        if role["inline"] or role["managed"] or role["profiles"]:
            raise RuntimeError("DeleteConflict: the role still has attachments")
        del self.roles[RoleName]


class FakeSSM:
    """Minimal in-memory stand-in for a boto3 SSM client (no AWS)."""

    def __init__(self, fail_put=False, fail_get=False):
        self.exceptions = _Exceptions
        self.params = {}
        self.fail_put = fail_put
        self.fail_get = fail_get

    def get_parameter(self, Name):
        if self.fail_get:
            raise RuntimeError("AccessDeniedException: not allowed to read the parameter")
        if Name not in self.params:
            raise _Exceptions.ParameterNotFound(Name)
        return {"Parameter": {"Name": Name, "Value": self.params[Name]}}

    def put_parameter(self, Name, Value, Type, Overwrite=False):
        if self.fail_put:
            raise RuntimeError("ThrottlingException: rate exceeded")
        self.params[Name] = Value

    def delete_parameter(self, Name):
        if Name not in self.params:
            raise _Exceptions.ParameterNotFound(Name)
        del self.params[Name]


class FakeLakeFormation:
    """Answers describe_resource for one resource (no AWS)."""

    def __init__(self, registered, role_arn=None, fail=False):
        self.exceptions = _Exceptions
        self.registered = registered
        self.role_arn = role_arn
        self.fail = fail

    def describe_resource(self, ResourceArn):
        if self.fail:
            raise RuntimeError("AccessDeniedException: not allowed to describe the resource")
        if not self.registered:
            raise _Exceptions.EntityNotFoundException(ResourceArn)
        info = {"ResourceArn": ResourceArn}
        if self.role_arn:
            info["RoleArn"] = self.role_arn
        return {"ResourceInfo": info}


_failures = []
_checks = 0


def check(name, condition):
    global _checks
    _checks += 1
    print(f"{'PASS' if condition else 'FAIL'}  {name}")
    if not condition:
        _failures.append(name)


def quiet(fn, *args, **kwargs):
    """Call fn with its printed output captured. Returns (result, output)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = fn(*args, **kwargs)
    return result, buffer.getvalue()


def refusal(fn, *args, **kwargs):
    """Return the RoleOwnershipError message fn raises, or None if it raises nothing."""
    try:
        fn(*args, **kwargs)
    except RoleOwnershipError as err:
        return str(err)
    return None


def create_ours(iam, ssm, name=ROLE):
    """Create a role the way a setup script does: ensure_role, then our own policies."""
    arn, created = ensure_role(iam, ssm, name, TRUST, description="test role")
    iam.put_role_policy(RoleName=name, PolicyName="OurPolicy", PolicyDocument="{}")
    iam.attach_role_policy(RoleName=name, PolicyArn=next(iter(OUR_MANAGED)))
    return arn, created


def test_no_aws_guard():
    try:
        boto3.client("iam")
        reached = True
    except NoAWSError:
        reached = False
    check("guard: a real boto3 client cannot be created", not reached)


def test_create_then_delete():
    iam, ssm = FakeIAM(), FakeSSM()
    _, created = create_ours(iam, ssm)
    role_id = iam.roles[ROLE]["RoleId"]
    check("normal: create reports created=True", created)
    check("normal: the RoleId is recorded in SSM", ssm.params.get(record_name(ROLE)) == role_id)
    code, _ = quiet(delete_owned_role, iam, ssm, ROLE, OUR_INLINE, OUR_MANAGED)
    check("normal: delete returns OK", code == OK)
    check("normal: the role is gone", ROLE not in iam.roles)
    check("normal: the ownership record is cleared", record_name(ROLE) not in ssm.params)


def test_rerun_on_own_role():
    iam, ssm = FakeIAM(), FakeSSM()
    first_arn, _ = create_ours(iam, ssm)
    role_id = iam.roles[ROLE]["RoleId"]
    iam.calls.clear()
    arn, created = ensure_role(iam, ssm, ROLE, TRUST, description="test role")
    check("re-run: our own role is re-used (created=False, same ARN)", not created and arn == first_arn)
    check("re-run: no new role was created", iam.roles[ROLE]["RoleId"] == role_id)
    check("re-run: the only IAM write is our trust policy", iam.writes() == [("update_assume_role_policy", ROLE)])


def test_collision_refused():
    iam, ssm = FakeIAM(), FakeSSM()
    iam.add_role(ROLE, inline={"TheirPolicy"})
    message = refusal(ensure_role, iam, ssm, ROLE, TRUST, description="test role")
    check("collision: refused with the 'already exists' message", bool(message) and "already exists" in message)
    check("collision: no IAM write", iam.writes() == [])
    check("collision: no ownership record written", ssm.params == {})
    check("collision: the other role is untouched", iam.roles[ROLE]["inline"] == {"TheirPolicy"})

    iam, ssm = FakeIAM(), FakeSSM()
    iam.add_role(ROLE)
    ssm.params[record_name(ROLE)] = "AROAOLDROLE0000"
    message = refusal(ensure_role, iam, ssm, ROLE, TRUST, description="test role")
    check("collision: a stale record for an older role does not count", bool(message) and iam.writes() == [])


def test_record_write_failure_rolls_back():
    iam, ssm = FakeIAM(), FakeSSM(fail_put=True)
    message = refusal(ensure_role, iam, ssm, ROLE, TRUST, description="test role")
    check(
        "record write fails: refused, saying the role was deleted again", bool(message) and "deleted again" in message
    )
    check("record write fails: the new role is gone", ROLE not in iam.roles)
    check(
        "record write fails: only create_role and delete_role ran",
        iam.writes() == [("create_role", ROLE), ("delete_role", ROLE)],
    )


def test_unreadable_record_changes_nothing():
    iam, ssm = FakeIAM(), FakeSSM(fail_get=True)
    iam.add_role(ROLE)
    message = refusal(ensure_role, iam, ssm, ROLE, TRUST, description="test role")
    check("SSM read error at setup: refused, no IAM write", bool(message) and iam.writes() == [])
    code, _ = quiet(delete_owned_role, iam, ssm, ROLE, OUR_INLINE, OUR_MANAGED)
    check("SSM read error at cleanup: FAILED, no IAM write", code == FAILED and iam.writes() == [])


def test_replacement_not_deleted():
    iam, ssm = FakeIAM(), FakeSSM()
    create_ours(iam, ssm)
    del iam.roles[ROLE]
    iam.add_role(ROLE, inline=OUR_INLINE)
    iam.calls.clear()
    code, output = quiet(delete_owned_role, iam, ssm, ROLE, OUR_INLINE, OUR_MANAGED)
    check("replacement: delete returns FAILED", code == FAILED)
    check("replacement: the message says it is not our role", "is not the role this sample created" in output)
    check("replacement: nothing was deleted", iam.writes() == [] and ROLE in iam.roles)


def test_unexpected_attachments_refused():
    extras = {
        "inline policy": ("inline", "OperatorAudit"),
        "managed policy": ("managed", "arn:aws:iam::aws:policy/ReadOnlyAccess"),
        "instance profile": ("profiles", "ec2-profile"),
    }
    for label, (kind, extra) in extras.items():
        iam, ssm = FakeIAM(), FakeSSM()
        create_ours(iam, ssm)
        iam.roles[ROLE][kind].add(extra)
        iam.calls.clear()
        code, output = quiet(delete_owned_role, iam, ssm, ROLE, OUR_INLINE, OUR_MANAGED)
        check(f"unexpected {label}: delete returns FAILED and names it", code == FAILED and extra in output)
        check(f"unexpected {label}: nothing was deleted", iam.writes() == [] and extra in iam.roles[ROLE][kind])


def test_records_gone():
    iam, ssm = FakeIAM(), FakeSSM()
    iam.add_role(ROLE, inline=OUR_INLINE)
    code, output = quiet(delete_owned_role, iam, ssm, ROLE, OUR_INLINE, OUR_MANAGED)
    check("no record, role exists: LEFT, left in place", code == LEFT and ROLE in iam.roles and iam.writes() == [])
    check("no record, role exists: the message gives the manual delete command", "aws iam delete-role" in output)

    iam, ssm = FakeIAM(), FakeSSM()
    ssm.params[record_name(ROLE)] = "AROAGONE0000000"
    code, _ = quiet(delete_owned_role, iam, ssm, ROLE, OUR_INLINE, OUR_MANAGED)
    check("record present, role gone: OK and the record is cleared", code == OK and ssm.params == {})

    iam, ssm = FakeIAM(), FakeSSM()
    code, _ = quiet(delete_owned_role, iam, ssm, ROLE, OUR_INLINE, OUR_MANAGED)
    check("no record, no role: OK", code == OK)


def test_lake_formation_owner():
    iam, ssm = FakeIAM(), FakeSSM()
    lf_arn, _ = ensure_role(iam, ssm, LF_ROLE, TRUST, description="test role")

    owner = lf_registration_owner(FakeLakeFormation(registered=False), iam, ssm, LF_RESOURCE, LF_ROLE)
    check("Lake Formation: not registered", owner == (NOT_REGISTERED, None))

    owner = lf_registration_owner(FakeLakeFormation(True, lf_arn), iam, ssm, LF_RESOURCE, LF_ROLE)
    check("Lake Formation: registered with our recorded role is ours", owner == (OURS, lf_arn))

    other = "arn:aws:iam::111122223333:role/SomeoneElsesRole"
    owner = lf_registration_owner(FakeLakeFormation(True, other), iam, ssm, LF_RESOURCE, LF_ROLE)
    check("Lake Formation: registered with another role is foreign", owner == (FOREIGN, other))

    owner = lf_registration_owner(FakeLakeFormation(True, None), iam, ssm, LF_RESOURCE, LF_ROLE)
    check("Lake Formation: the service-linked role is foreign", owner == (FOREIGN, "service-linked"))

    iam2, ssm2 = FakeIAM(), FakeSSM()
    iam2.add_role(LF_ROLE)
    same_name = f"arn:aws:iam::111122223333:role/{LF_ROLE}"
    owner = lf_registration_owner(FakeLakeFormation(True, same_name), iam2, ssm2, LF_RESOURCE, LF_ROLE)
    check("Lake Formation: our role name without our record is foreign", owner == (FOREIGN, same_name))

    message = refusal(lf_registration_owner, FakeLakeFormation(True, fail=True), iam, ssm, LF_RESOURCE, LF_ROLE)
    check("Lake Formation: an unreadable registration raises", bool(message) and "Nothing was changed" in message)
    check("Lake Formation: the checks made no IAM write after setup", iam.writes() == [("create_role", LF_ROLE)])


def main():
    test_no_aws_guard()
    test_create_then_delete()
    test_rerun_on_own_role()
    test_collision_refused()
    test_record_write_failure_rolls_back()
    test_unreadable_record_changes_nothing()
    test_replacement_not_deleted()
    test_unexpected_attachments_refused()
    test_records_gone()
    test_lake_formation_owner()
    print(f"\n{_checks} checks, {len(_failures)} failed")
    for name in _failures:
        print(f"   ❌ {name}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
