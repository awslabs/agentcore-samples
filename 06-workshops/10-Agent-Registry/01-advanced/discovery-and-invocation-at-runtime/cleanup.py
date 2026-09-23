"""
Cleanup script for 05_agentic_consumer_discovery.ipynb
Deletes all AWS resources created during the notebook demo in reverse order.

Usage (from notebook):
    %run cleanup.py

All variables are expected to come from the notebook kernel. Each section
is guarded so cleanup proceeds even if the kernel was restarted mid-demo.
"""

import os
import time
import shutil
import boto3

# Re-create AWS clients in case kernel was restarted
try:
    cp_client
except NameError:
    session = boto3.Session()
    region = session.region_name or "us-west-2"
    cp_client = session.client("bedrock-agentcore-control")
    iam_client = session.client("iam")
    lambda_client = session.client("lambda")
    cognito_client = session.client("cognito-idp")
    sm_client = session.client("secretsmanager")


# Safely resolve notebook variables — if the kernel was restarted, some may be missing.
# NOTE: We use direct variable references with try/except instead of locals().get()
# because %run -i injects notebook variables into the execution namespace but
# locals().get() / globals().get() may not find them reliably in all IPython versions.
def _safe_get(name):
    """Get a variable from the notebook namespace, returning None if not set."""
    # Try IPython's user namespace first (most reliable for %run -i)
    try:
        ip = get_ipython()
        if name in ip.user_ns:
            return ip.user_ns[name]
    except NameError:
        pass
    # Fallback to frame locals/globals
    import inspect

    frame = inspect.currentframe().f_back
    try:
        if name in frame.f_locals:
            return frame.f_locals[name]
        if name in frame.f_globals:
            return frame.f_globals[name]
    finally:
        del frame
    return None


_orchestrator_agent_id = _safe_get("orchestrator_agent_id")
_pricing_agent_id = _safe_get("pricing_agent_id")
_support_agent_id = _safe_get("support_agent_id")
_record_ids = _safe_get("record_ids")
_REGISTRY_ID = _safe_get("REGISTRY_ID")
_target_ids = _safe_get("target_ids")
_gateway_id = _safe_get("gateway_id")
_lambda_arns = _safe_get("lambda_arns")
_lambda_role_name = _safe_get("lambda_role_name")
_gateway_role_name = _safe_get("gateway_role_name")
_secret_name = _safe_get("secret_name")
_domain_name = _safe_get("domain_name")
_user_pool_id = _safe_get("user_pool_id")
_orchestrator_launch = _safe_get("orchestrator_launch")
_pricing_launch = _safe_get("pricing_launch")
_support_launch = _safe_get("support_launch")

print("=== Cleanup ===\n")

_agent_ids = [
    ("orchestrator", _orchestrator_agent_id),
    ("pricing", _pricing_agent_id),
    ("support", _support_agent_id),
]

# 1. Delete registry records
print("\n1. Deleting registry records...")
if _record_ids and _REGISTRY_ID:
    for rid in _record_ids:
        try:
            cp_client.delete_registry_record(registryId=_REGISTRY_ID, recordId=rid)
            print(f"  Deleted record: {rid}")
        except Exception as e:
            print(f"  Skip {rid}: {e}")
else:
    print("  Skipped (record_ids or REGISTRY_ID not set)")

# 2. Delete registry
print("\n2. Deleting registry...")
if _REGISTRY_ID:
    try:
        cp_client.delete_registry(registryId=_REGISTRY_ID)
        print(f"  Deleted registry: {_REGISTRY_ID}")
    except Exception as e:
        print(f"  Skip: {e}")
else:
    print("  Skipped (REGISTRY_ID not set)")

# 3. Delete A2A agents
print("\n3. Deleting A2A agents...")
for name, aid in _agent_ids:
    if not aid:
        print(f"  {name}: skipped (variable not set)")
        continue
    try:
        cp_client.delete_agent_runtime(agentRuntimeId=aid)
        print(f"  Deleted agent: {aid}")
    except Exception as e:
        print(f"  Skip {name}: {e}")

# 4. Delete gateway targets
print("\n4. Deleting gateway targets...")
if _target_ids and _gateway_id:
    for tname, tid in _target_ids.items():
        try:
            cp_client.delete_gateway_target(gatewayIdentifier=_gateway_id, targetId=tid)
            print(f"  Deleted target: {tid}")
        except Exception as e:
            print(f"  Skip {tname}: {e}")
    time.sleep(30)  # Wait for targets to delete
else:
    print("  Skipped (target_ids or gateway_id not set)")

# 5. Delete gateway
print("\n5. Deleting gateway...")
if _gateway_id:
    try:
        cp_client.delete_gateway(gatewayIdentifier=_gateway_id)
        print(f"  Deleted gateway: {_gateway_id}")
    except Exception as e:
        print(f"  Skip: {e}")
else:
    print("  Skipped (gateway_id not set)")

# 6. Delete Lambda functions
print("\n6. Deleting Lambda functions...")
if _lambda_arns:
    for name, arn in _lambda_arns.items():
        try:
            lambda_client.delete_function(FunctionName=arn)
            print(f"  Deleted: {name}")
        except Exception as e:
            print(f"  Skip {name}: {e}")
else:
    print("  Skipped (lambda_arns not set)")

# 7. Delete IAM roles
print("\n7. Deleting IAM roles...")
for role_name in [_lambda_role_name, _gateway_role_name]:
    if not role_name:
        continue
    try:
        for p in iam_client.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]:
            iam_client.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
        for p in iam_client.list_role_policies(RoleName=role_name)["PolicyNames"]:
            iam_client.delete_role_policy(RoleName=role_name, PolicyName=p)
        iam_client.delete_role(RoleName=role_name)
        print(f"  Deleted role: {role_name}")
    except Exception as e:
        print(f"  Skip {role_name}: {e}")

# 8. Delete Secrets Manager secret
print("\n8. Deleting Secrets Manager secret...")
if _secret_name:
    try:
        sm_client.delete_secret(SecretId=_secret_name, ForceDeleteWithoutRecovery=True)
        print(f"  Deleted secret: {_secret_name}")
    except Exception as e:
        print(f"  Skip: {e}")
else:
    print("  Skipped (secret_name not set)")

# 9. Delete Cognito
print("\n9. Deleting Cognito...")
if _domain_name and _user_pool_id:
    try:
        cognito_client.delete_user_pool_domain(Domain=_domain_name, UserPoolId=_user_pool_id)
        cognito_client.delete_user_pool(UserPoolId=_user_pool_id)
        print(f"  Deleted pool: {_user_pool_id}")
    except Exception as e:
        print(f"  Skip: {e}")
else:
    print("  Skipped (domain_name or user_pool_id not set)")

# 10. Clean up local files
print("\n10. Cleaning up local files...")
for f in [
    "pricing_agent.py",
    "customer_support_agent.py",
    "orchestrator_agent.py",
    "a2a_requirements.txt",
    "orchestrator_requirements.txt",
    ".bedrock_agentcore.yaml",
    "Dockerfile",
    ".dockerignore",
]:
    if os.path.exists(f):
        os.remove(f)
        print(f"  Removed: {f}")
if os.path.exists("models"):
    shutil.rmtree("models")

# 11. Delete ECR repositories created by starter toolkit
print("\n11. Deleting ECR repositories...")
ecr_client = session.client("ecr")
for launch in [_orchestrator_launch, _pricing_launch, _support_launch]:
    if not launch:
        continue
    try:
        repo_name = launch.ecr_uri.split("/")[1].split(":")[0]
        ecr_client.delete_repository(repositoryName=repo_name, force=True)
        print(f"  Deleted ECR repo: {repo_name}")
    except Exception as e:
        print(f"  Skip ECR: {e}")

print("\n=== Cleanup complete! ===")
