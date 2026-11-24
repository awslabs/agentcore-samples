#!/usr/bin/env python3
"""
Ownership checks for the IAM roles this sample creates by fixed name.

The deployment scripts create their IAM roles under fixed names, so a role with
the same name may already exist in the account and belong to someone else. A
name alone does not prove which role is which: IAM gives every role a unique
RoleId, and a role that is deleted and created again under the same name gets a
new one. So when this sample creates a role it records that RoleId in SSM
Parameter Store, one String parameter per role:

    /app/lakehouse-agent/role-ids/<role name> = <RoleId>

and it only re-uses or deletes a role whose current RoleId matches its record.

    ensure_role        create the role and record it; on a re-run, re-use it only
                       if the record matches, otherwise refuse with no IAM write
    is_owned           True / False / None for "ours" / "someone else's" / "missing"
    delete_owned_role  delete the role only if the record matches and every
                       attachment is one this sample adds; returns OK / FAILED / LEFT
    combine            fold several of those return codes into one exit code
    lf_registration_owner  who owns the Lake Formation registration of a resource:
                       not registered, this sample (by RoleId), or someone else

Exit codes: OK (0) success, FAILED (1) an error or a refusal, LEFT (3) the only
problem was a role left in place because this sample could not prove it created it.

Deploy-time helper only. Scripts import it after adding the sample root to
``sys.path``::

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
    from utils.iam_roles import ensure_role, delete_owned_role
"""

import json
from collections.abc import Callable, Iterable
from typing import Any

RECORD_PREFIX = "/app/lakehouse-agent/role-ids/"

OK, FAILED, LEFT = 0, 1, 3


class RoleOwnershipError(Exception):
    """Raised when this sample must not, or could not, take ownership of a role.

    ``str()`` of the exception is the full message to print.
    """


def record_name(role_name: str) -> str:
    """Return the SSM parameter name that holds the RoleId recorded for ``role_name``."""
    return f"{RECORD_PREFIX}{role_name}"


def read_record(ssm, role_name: str) -> str | None:
    """Return the recorded RoleId for ``role_name``, or None when there is no record.

    Only ``ParameterNotFound`` means "no record". Any other SSM error raises
    RoleOwnershipError, because treating an unreadable record as a missing one
    would turn a permissions or throttling error into a wrong ownership answer.
    """
    try:
        return ssm.get_parameter(Name=record_name(role_name))["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return None
    except Exception as err:
        raise RoleOwnershipError(
            f"❌ Could not read the ownership record for IAM role {role_name}: {err}. Nothing was changed."
        ) from err


def ensure_role(
    iam,
    ssm,
    role_name: str,
    trust_policy: dict,
    *,
    description: str,
    tags: list | None = None,
    create_role_fn: Callable[[str, dict], dict] | None = None,
) -> tuple[str, bool]:
    """Create ``role_name`` and record its RoleId, or re-use it if this sample created it.

    Args:
        iam: boto3 IAM client.
        ssm: boto3 SSM client.
        role_name: The fixed role name.
        trust_policy: Trust policy document (a dict) for the role.
        description: Role description, used only when the role is created.
        tags: Tags for create_role, used only when the role is created.
        create_role_fn: Optional ``fn(role_name, trust_policy)`` that creates the
            role and returns the boto3 ``create_role`` response. Used instead of
            ``iam.create_role`` when given.

    Returns:
        ``(role_arn, created)``. ``created`` is True when this call created the role.

    Raises:
        RoleOwnershipError: a same-name role exists without a matching record (no
            IAM write was made), the record could not be read, or the record could
            not be written for a new role (the new role was deleted again).

    The caller attaches its own policies afterwards. The record is written before
    any policy is attached, so a crash never leaves an unrecorded role with
    permissions.
    """
    try:
        if create_role_fn is not None:
            response = create_role_fn(role_name, trust_policy)
        else:
            kwargs: dict[str, Any] = {
                "RoleName": role_name,
                "AssumeRolePolicyDocument": json.dumps(trust_policy),
                "Description": description,
            }
            # boto3's parameter validation rejects Tags=None, so the key is passed only when there are tags.
            if tags:
                kwargs["Tags"] = tags
            response = iam.create_role(**kwargs)
    except iam.exceptions.EntityAlreadyExistsException:
        recorded = read_record(ssm, role_name)
        role = iam.get_role(RoleName=role_name)["Role"]
        if recorded is not None and recorded == role["RoleId"]:
            iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust_policy))
            return role["Arn"], False
        raise RoleOwnershipError(
            f"❌ IAM role {role_name} already exists and this sample has no matching ownership record "
            "for it. Nothing was changed. Delete or rename that role, then re-run this step."
        ) from None

    role = response["Role"]
    try:
        ssm.put_parameter(Name=record_name(role_name), Value=role["RoleId"], Type="String", Overwrite=True)
    except Exception as err:
        try:
            iam.delete_role(RoleName=role_name)
            rollback = "The role was deleted again."
        except Exception as err2:
            rollback = (
                f"It could not be deleted ({err2}); delete it by hand: aws iam delete-role --role-name {role_name}."
            )
        raise RoleOwnershipError(
            f"❌ Could not record ownership of new IAM role {role_name}: {err}. {rollback} "
            "Fix the error, then re-run this step."
        ) from err
    return role["Arn"], True


def is_owned(iam, ssm, role_name: str) -> bool | None:
    """Return True if the role exists and its RoleId matches the record, False if it
    exists without a matching record, None if the role does not exist.

    Raises RoleOwnershipError when the record cannot be read.
    """
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
    except iam.exceptions.NoSuchEntityException:
        return None
    recorded = read_record(ssm, role_name)
    return recorded is not None and recorded == role["RoleId"]


NOT_REGISTERED, OURS, FOREIGN = "not-registered", "ours", "foreign"


def lf_registration_owner(lakeformation, iam, ssm, resource_arn: str, role_name: str) -> tuple[str, str | None]:
    """Say who owns the Lake Formation registration of ``resource_arn``.

    Returns ``(NOT_REGISTERED, None)``, ``(OURS, role ARN)`` or ``(FOREIGN, role ARN)``.
    A registration made with the service-linked role has no RoleArn and is reported
    as ``(FOREIGN, "service-linked")``. The registration is ours only when it names
    ``role_name`` and that role's RoleId matches this sample's ownership record.

    Raises:
        RoleOwnershipError: the registration or the role could not be read, so the
            caller must not change anything.
    """
    try:
        info = lakeformation.describe_resource(ResourceArn=resource_arn)["ResourceInfo"]
    except lakeformation.exceptions.EntityNotFoundException:
        return NOT_REGISTERED, None
    except Exception as err:
        raise RoleOwnershipError(
            f"❌ Could not read the Lake Formation registration for {resource_arn}: {err}. Nothing was changed."
        ) from err
    role_arn = info.get("RoleArn")
    if not role_arn:
        return FOREIGN, "service-linked"
    if role_arn.split("/")[-1] != role_name:
        return FOREIGN, role_arn
    try:
        owned = is_owned(iam, ssm, role_name)
    except RoleOwnershipError:
        raise
    except Exception as err:
        raise RoleOwnershipError(f"❌ Could not check IAM role {role_name}: {err}. Nothing was changed.") from err
    return (OURS, role_arn) if owned is True else (FOREIGN, role_arn)


def _list_all(call: Callable[..., dict], result_key: str, role_name: str) -> list:
    """Collect every page of an IAM list call (these calls page with Marker/IsTruncated)."""
    items: list = []
    kwargs: dict[str, Any] = {"RoleName": role_name}
    while True:
        page = call(**kwargs)
        items.extend(page[result_key])
        if not page.get("IsTruncated"):
            return items
        kwargs["Marker"] = page["Marker"]


def _delete_record(ssm, role_name: str) -> None:
    """Delete the ownership record; a record that is already gone is not an error."""
    try:
        ssm.delete_parameter(Name=record_name(role_name))
    except ssm.exceptions.ParameterNotFound:
        pass


def delete_owned_role(iam, ssm, role_name: str, expected_inline: set[str], expected_managed: set[str]) -> int:
    """Delete ``role_name`` only if this sample created it and nothing else is attached.

    Args:
        iam: boto3 IAM client.
        ssm: boto3 SSM client.
        role_name: The fixed role name.
        expected_inline: Inline policy names this sample puts on the role.
        expected_managed: Managed policy ARNs this sample attaches to the role.

    Returns:
        OK when the role was deleted or is already gone, FAILED on an error or a
        refusal, LEFT when the role exists without an ownership record and was
        left in place.
    """
    try:
        recorded = read_record(ssm, role_name)
    except RoleOwnershipError as err:
        print(f"   {err}")
        return FAILED

    try:
        try:
            role = iam.get_role(RoleName=role_name)["Role"]
        except iam.exceptions.NoSuchEntityException:
            if recorded is None:
                print(f"   ⏭  Role not found: {role_name}")
                return OK
            _delete_record(ssm, role_name)
            print(f"   ⏭  Role already deleted: {role_name} (ownership record cleared)")
            return OK

        if recorded is None:
            print(
                f"   ⚠  IAM role {role_name} exists but this sample has no ownership record for it, so it was "
                f"left in place. If it is yours, remove its policies and run: aws iam delete-role --role-name {role_name}"
            )
            return LEFT
        if recorded != role["RoleId"]:
            print(
                f"   ❌ IAM role {role_name} is not the role this sample created (its role id does not match "
                "the ownership record). Nothing was deleted."
            )
            return FAILED

        inline = set(_list_all(iam.list_role_policies, "PolicyNames", role_name))
        managed = {p["PolicyArn"] for p in _list_all(iam.list_attached_role_policies, "AttachedPolicies", role_name)}
        profiles = {
            p["InstanceProfileName"]
            for p in _list_all(iam.list_instance_profiles_for_role, "InstanceProfiles", role_name)
        }
        unexpected = (inline - set(expected_inline)) | (managed - set(expected_managed)) | profiles
        if unexpected:
            print(
                f"   ❌ IAM role {role_name} has attachments this sample did not add: {', '.join(sorted(unexpected))}. "
                "Nothing was deleted. Remove them, or delete the role by hand."
            )
            return FAILED

        for policy_name in sorted(inline):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        for policy_arn in sorted(managed):
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        iam.delete_role(RoleName=role_name)
        _delete_record(ssm, role_name)
        print(f"   ✅ Deleted role: {role_name}")
        return OK
    except Exception as err:
        print(f"   ❌ Error deleting {role_name}: {err}")
        return FAILED


def combine(codes: Iterable[int]) -> int:
    """Fold return codes into one exit code: FAILED if any failed, else LEFT if any
    role was left in place, else OK. Any code other than OK or LEFT counts as FAILED."""
    codes = list(codes)
    if any(code not in (OK, LEFT) for code in codes):
        return FAILED
    if LEFT in codes:
        return LEFT
    return OK
