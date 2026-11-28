#!/usr/bin/env python3
"""Target-bound operator commands. This module makes AWS calls only from main().

A fresh deployment reserves a NEW CloudFormation stack using CreateStack (never
UpdateStack), journals its StackId, and verifies owner tags before CLI deployment.
Existing/unproven local or remote state is not adoptable. Failed attempts retain
the ledger; use destroy for a proven reservation, not another fresh deployment.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ".sample-deployment.json"


def save(path, value):
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def cli_environment(root):
    """Private CLI preferences/key storage, without copying credentials or state.

    CLI 1.2.0 reads transactionSearch from HOME/.agentcore/config.json, not a
    deploy flag. Preserve explicit AWS file REFERENCES for the operator's auth.
    Never import a credential cache into this private directory.
    """
    home = Path(root) / ".sample-cli-home"
    config = home / ".agentcore"
    if home.is_symlink() or config.is_symlink():
        raise ValueError("Private CLI directories must not redirect to another config store")
    home.mkdir(mode=0o700, exist_ok=True)
    config.mkdir(mode=0o700, exist_ok=True)
    home.chmod(0o700)
    config.chmod(0o700)
    save(
        config / "config.json",
        {"disableDependencyManagement": True, "transactionSearch": False, "telemetry": {"enabled": False}},
    )
    return dict(
        os.environ,
        HOME=str(home),
        AGENTCORE_CONFIG_DIR=str(config),
        AGENTCORE_DISABLE_KEYCHAIN="1",
        AWS_CONFIG_FILE=os.environ.get("AWS_CONFIG_FILE", str(Path.home() / ".aws/config")),
        AWS_SHARED_CREDENTIALS_FILE=os.environ.get(
            "AWS_SHARED_CREDENTIALS_FILE", str(Path.home() / ".aws/credentials")
        ),
    )


def run(command, timeout=60, env=None):
    result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, env=env, check=False)
    if result.returncode:
        # AWS diagnostics are retained locally, never treat denial as absence.
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command[:3])}\n{result.stderr}")
    return result.stdout


class Deployment:
    def __init__(self, root, project, target, account, region):
        self.root = Path(root)
        self.path = self.root / LEDGER
        self.state_path = self.root / "agentcore/.cli/deployed-state.json"
        self.spec = json.loads((self.root / "agentcore/agentcore.json").read_text())
        if self.spec.get("version") != 2 or self.spec["name"] != project:
            raise ValueError("Project/version mismatch; do not rename a deployed project")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,23}", project):
            raise ValueError("Use a unique project name of at most 24 characters")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", target) or not re.fullmatch(r"\d{12}", account):
            raise ValueError("Invalid explicit target/account")
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region):
            raise ValueError("Invalid explicit region")
        for key in ("AWS_REGION", "AWS_DEFAULT_REGION", "CDK_DEFAULT_REGION", "DEPLOY_REGION"):
            if os.environ.get(key) and os.environ[key] != region:
                raise ValueError(f"{key} conflicts with selected region")
        if os.environ.get("CDK_DEFAULT_ACCOUNT", account) != account:
            raise ValueError("CDK_DEFAULT_ACCOUNT conflicts with selected account")
        self.binding = {
            "project": project,
            "target": target,
            "account": account,
            "region": region,
            "stack_name": f"AgentCore-{project.replace('_', '-')}-{target}",
        }
        if os.environ.get("STACK_NAME", self.binding["stack_name"]) != self.binding["stack_name"]:
            raise ValueError("STACK_NAME override is not permitted")
        targets_path = self.root / "agentcore/aws-targets.json"
        targets = json.loads(targets_path.read_text()) if targets_path.exists() else []
        if len(targets) != 1 or any(
            targets[0].get(k) != v for k, v in {"name": target, "account": account, "region": region}.items()
        ):
            raise ValueError("Configure exactly one matching aws-targets.json entry before operating")
        self.ledger = json.loads(self.path.read_text()) if self.path.exists() else None
        if self.ledger and self.ledger.get("binding") != self.binding:
            raise ValueError("Ledger project/target/account/region mismatch")

    def aws(self, *args, timeout=60):
        text = run(["aws", *args, "--region", self.binding["region"], "--output", "json", "--no-cli-pager"], timeout)
        return json.loads(text) if text.strip() else {}

    def identity(self):
        actual = self.aws("sts", "get-caller-identity")
        if actual.get("Account") != self.binding["account"]:
            raise ValueError("Active AWS identity is in the wrong account")
        self.partition = actual["Arn"].split(":")[1]

    def describe(self, stack_id):
        # Do not turn ValidationError/denial into not-found; caller handles only
        # the precise AWS nonexistent-stack diagnostic where absence is expected.
        stacks = self.aws("cloudformation", "describe-stacks", "--stack-name", stack_id)["Stacks"]
        if len(stacks) != 1:
            raise ValueError("Expected exactly one stack")
        return stacks[0]

    def owned(self, require_deployed=True):
        self.identity()
        if not self.ledger or not self.ledger.get("stack_id"):
            raise ValueError("Missing proven deployment ledger/StackId; no mutation allowed")
        sid = self.ledger["stack_id"]
        prefix = f"arn:{self.partition}:cloudformation:{self.binding['region']}:{self.binding['account']}:stack/{self.binding['stack_name']}/"
        if not sid.startswith(prefix):
            raise ValueError("Recorded StackId mismatch")
        stack = self.describe(sid)
        tags = {t["Key"]: t["Value"] for t in stack.get("Tags", [])}
        if (
            stack.get("StackId") != sid
            or stack.get("StackName") != self.binding["stack_name"]
            or any(tags.get(k) != v for k, v in self.tags().items())
        ):
            raise ValueError("Actual stack identity/ownership mismatch")
        if require_deployed:
            if self.ledger["status"] != "DEPLOYED" or stack["StackStatus"] not in (
                "CREATE_COMPLETE",
                "UPDATE_COMPLETE",
            ):
                raise ValueError("Deployment is not complete")
            self.cli_state(require_arn=True)
        return stack

    def cli_state(self, require_arn=False):
        if not self.state_path.exists():
            raise ValueError("CLI state missing after CLI dispatch; restore it or investigate, do not guess")
        state = json.loads(self.state_path.read_text())
        record = state.get("targets", {}).get(self.binding["target"])
        if not isinstance(record, dict):
            raise ValueError("CLI state has no selected target")  # noqa: TRY004 -- invalid stored ownership state
        arn = record.get("stackArn")
        if (require_arn and not arn) or (arn and arn != self.ledger["stack_id"]):
            raise ValueError("CLI state StackId mismatch")
        if not arn and not isinstance(record.get("resources", {}).get("credentials"), dict):
            raise ValueError("Unproven partial CLI state")
        credentials = record.get("resources", {}).get("credentials", {})
        for value in credentials.values():
            for key in ("credentialProviderArn", "clientSecretArn"):
                if value.get(key) and value[key].split(":")[3:5] != [self.binding["region"], self.binding["account"]]:
                    raise ValueError("Recorded credential account/region mismatch")
        return record

    def tags(self):
        return {
            "agentcore:project-name": self.binding["project"],
            "agentcore:target-name": self.binding["target"],
            "it-sample:owner": self.ledger["nonce"],
        }

    def resources(self):
        rows = []
        token = None
        for _ in range(20):
            page = self.aws(
                "cloudformation",
                "list-stack-resources",
                "--stack-name",
                self.ledger["stack_id"],
                *(["--next-token", token] if token else []),
                "--no-paginate",
            )
            rows.extend(page.get("StackResourceSummaries", []))
            token = page.get("NextToken")
            if not token:
                return rows
        raise RuntimeError("Resource inventory pagination exceeded bound")

    def checkpoint(self):
        save(self.path, self.ledger)

    def record_inventory(self, stack):
        self.ledger["resources"] = self.resources()
        self.ledger["outputs"] = stack.get("Outputs", [])
        if self.state_path.exists():
            credentials = self.cli_state().get("resources", {}).get("credentials", {})
            self.ledger["cli_credentials"] = {
                name: {k: v for k, v in value.items() if k in ("credentialProviderArn", "clientSecretArn")}
                for name, value in credentials.items()
            }
        # Rollback can remove retained resources from ListStackResources. Preserve
        # the bounded event inventory as well, particularly the Jira proof table.
        page = self.aws(
            "cloudformation", "describe-stack-events", "--stack-name", self.ledger["stack_id"], "--max-items", "1000"
        )
        if page.get("NextToken"):
            raise RuntimeError("Stack event inventory incomplete; retain state and investigate")
        keys = {
            "EventId",
            "StackId",
            "LogicalResourceId",
            "PhysicalResourceId",
            "ResourceType",
            "ResourceStatus",
            "ResourceStatusReason",
            "Timestamp",
        }
        events = [{k: v for k, v in event.items() if k in keys} for event in page.get("StackEvents", [])]
        self.ledger["events"] = events
        candidates = self.ledger.get("retained_candidates", []) + [
            e
            for e in events
            if e.get("PhysicalResourceId")
            and (
                e.get("ResourceStatus") == "DELETE_SKIPPED"
                or e.get("LogicalResourceId", "").startswith("JiraProviderOwnership")
            )
        ]
        self.ledger["retained_candidates"] = list(
            {(e["ResourceType"], e["PhysicalResourceId"]): e for e in candidates}.values()
        )
        self.checkpoint()

    def fresh(self):
        # Reject even empty or malformed state. No reset/migration escape hatch.
        if self.ledger or (self.root / "agentcore/.cli").exists():
            raise ValueError("Fresh-only: existing or unproven local state; do not erase it")
        if self.spec.get("onlineEvalConfigs") and os.environ.get("IT_SAMPLE_EVALUATION_CONTENT") != "synthetic-only":
            raise ValueError(
                "Evaluation requires explicit IT_SAMPLE_EVALUATION_CONTENT=synthetic-only acknowledgement; traces may contain content"
            )
        cli = os.environ.get("AGENTCORE_CLI", "agentcore")
        if run([cli, "--version"], env=cli_environment(self.root)).strip() != "1.2.0":
            raise ValueError("Use AgentCore CLI 1.2.0")
        self.identity()
        # List instead of interpreting arbitrary errors as proof of absence.
        existing = self.aws("cloudformation", "list-stacks")
        project_prefix = f"AgentCore-{self.binding['project'].replace('_', '-')}-"
        if any(s["StackName"].startswith(project_prefix) for s in existing.get("StackSummaries", [])):
            raise ValueError(
                "Project stack name already used, including other targets/deleted history; choose a fresh project"
            )
        if existing.get("NextToken"):
            raise ValueError("Incomplete stack listing; cannot prove freshness")
        # CLI migrated naming keeps bare credential names. Preflight each name;
        # the project-qualified name also avoids the historical auth0-m2m collision.
        for credential in self.spec.get("credentials", []):
            if not credential["name"].startswith(self.binding["project"] + "-"):
                raise ValueError("Credential name must be unique and project-qualified")
            if credential["authorizerType"] != "OAuthCredentialProvider":
                raise ValueError("This scenario supports only the explicit Gateway OAuth credential")
            providers = self.aws("bedrock-agentcore-control", "list-oauth2-credential-providers")
            if providers.get("nextToken") or any(
                x["name"] == credential["name"] for x in providers.get("credentialProviders", [])
            ):
                raise ValueError("Credential already exists or listing incomplete; no adoption")
        self.ledger = {
            "version": 1,
            "binding": self.binding,
            "nonce": uuid.uuid4().hex,
            "status": "CREATE_ATTEMPTED",
            "created": time.time(),
        }
        self.checkpoint()  # Preserve even a timeout/ambiguous create attempt.
        # An empty, unnamed topic gives CFN a real new-stack reservation without
        # a WaitConditionHandle's credential-like presigned URL. No subscription
        # or publish operation is attached to this temporary resource.
        anchor = {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Resources": {"FreshReservation": {"Type": "AWS::SNS::Topic"}},
        }
        response = self.aws(
            "cloudformation",
            "create-stack",
            "--stack-name",
            self.binding["stack_name"],
            "--template-body",
            json.dumps(anchor),
            "--tags",
            json.dumps([{"Key": k, "Value": v} for k, v in self.tags().items()]),
            "--client-request-token",
            self.ledger["nonce"],
            "--on-failure",
            "DO_NOTHING",
        )
        self.ledger.update(stack_id=response["StackId"], status="RESERVED")
        self.checkpoint()
        self.aws("cloudformation", "wait", "stack-create-complete", "--stack-name", response["StackId"], timeout=600)
        self.owned(require_deployed=False)
        self.deploy_cli()

    def deploy_cli(self):
        cli = os.environ.get("AGENTCORE_CLI", "agentcore")
        env = dict(
            cli_environment(self.root),
            IT_SAMPLE_OWNER=self.ledger["nonce"],
            IT_SAMPLE_TARGET=self.binding["target"],
            IT_SAMPLE_SYNTH_ONLY="false",
        )
        if run([cli, "--version"], env=env).strip() != "1.2.0":
            raise ValueError("Use AgentCore CLI 1.2.0")
        if run([cli, "config", "transactionSearch"], env=env).strip() != "false":
            raise ValueError("Native CLI did not read the private transactionSearch=false preference")
        self.owned(require_deployed=False)
        # The custom resource, not native CLI best-effort setup, owns readiness.
        self.ledger["cli_started"] = True
        self.checkpoint()
        result = subprocess.run(
            [cli, "deploy", "--target", self.binding["target"], "--skip-bootstrap"],
            cwd=self.root,
            env=env,
            timeout=3600,
            check=False,  # Inventory below must run even when the CLI exits nonzero.
        )
        # Inventory even on CLI failure; never discard CLI/ledger evidence.
        stack = self.owned(require_deployed=False)
        self.record_inventory(stack)
        if result.returncode:
            self.ledger["status"] = "DEPLOY_FAILED"
            self.checkpoint()
            raise RuntimeError("CLI deploy failed; reservation and state retained for cleanup")
        self.ledger["status"] = "DEPLOYED"
        self.checkpoint()
        self.owned()
        print(f"Deployed owned StackId: {self.ledger['stack_id']}")

    def rotate_jira(self, version, confirm):
        stack = self.owned()
        if confirm != stack["StackId"] or not re.fullmatch(r"[A-Za-z0-9-]{32,64}", version or ""):
            raise ValueError("Rotation requires exact StackId confirmation and immutable secret VersionId")
        versions = [p for p in stack.get("Parameters", []) if p["ParameterKey"] == "JiraSecretVersionId"]
        arns = [o["OutputValue"] for o in stack.get("Outputs", []) if o["OutputKey"] == "JiraExternalSecretArn"]
        if len(versions) != 1 or len(arns) != 1:
            raise ValueError("Jira external-secret configuration is not present on this stack")
        expected_prefix = (
            f"arn:{self.partition}:secretsmanager:{self.binding['region']}:{self.binding['account']}:secret:"
        )
        if not arns[0].startswith(expected_prefix):
            raise ValueError("Actual Jira secret account/region mismatch")
        secret = self.aws("secretsmanager", "describe-secret", "--secret-id", arns[0])
        if secret.get("ARN") != arns[0] or version not in secret.get("VersionIdsToStages", {}):
            raise ValueError("Requested version is not observable on the recorded external secret")
        parameters = [
            {
                "ParameterKey": p["ParameterKey"],
                **(
                    {"ParameterValue": version}
                    if p["ParameterKey"] == "JiraSecretVersionId"
                    else {"UsePreviousValue": True}
                ),
            }
            for p in stack["Parameters"]
        ]
        self.ledger["rotation"] = {"from": versions[0]["ParameterValue"], "to": version, "status": "ATTEMPTED"}
        self.checkpoint()
        self.aws(
            "cloudformation",
            "update-stack",
            "--stack-name",
            stack["StackId"],
            "--use-previous-template",
            "--parameters",
            json.dumps(parameters),
            "--capabilities",
            "CAPABILITY_IAM",
            "CAPABILITY_NAMED_IAM",
        )
        self.aws("cloudformation", "wait", "stack-update-complete", "--stack-name", stack["StackId"], timeout=900)
        observed = self.owned()
        if not any(
            p["ParameterKey"] == "JiraSecretVersionId" and p.get("ParameterValue") == version
            for p in observed.get("Parameters", [])
        ):
            raise RuntimeError("Rotated secret version is not the observed stack parameter")
        self.ledger["rotation"]["status"] = "CONFIRMED"
        self.checkpoint()
        print("Owned Jira secret-version update confirmed; live token/consent verification is separate")

    def destroy(self, confirm):
        stack = self.owned(require_deployed=False)
        # Only a reservation proven not to have dispatched CLI may lack CLI state.
        if self.ledger["status"] != "RESERVED" or self.ledger.get("cli_started"):
            self.cli_state(require_arn=self.ledger["status"] == "DEPLOYED")
        sid = stack["StackId"]
        if confirm != sid:
            raise ValueError(f"Confirm the full StackId using --confirm-stack-id {sid}")
        if "IN_PROGRESS" in stack["StackStatus"]:
            raise ValueError("Stack operation still running; wait before cleanup")
        self.record_inventory(stack)
        self.ledger["status"] = "DELETE_ATTEMPTED"
        self.checkpoint()
        if stack["StackStatus"] != "DELETE_COMPLETE":
            self.aws("cloudformation", "delete-stack", "--stack-name", sid)
            self.aws("cloudformation", "wait", "stack-delete-complete", "--stack-name", sid, timeout=900)
        if self.describe(sid)["StackStatus"] != "DELETE_COMPLETE":
            raise RuntimeError("Stack deletion not confirmed")
        self.ledger["status"] = "STACK_DELETED_RETAINED_REVIEW_REQUIRED"
        self.checkpoint()
        self.verify_jira_absence()
        print(
            "Stack deletion confirmed. Ledger and CLI state retained. Review retained resources/credentials separately; cleanup is not complete."
        )

    def verify_jira_absence(self):
        # CDK Provider deliberately ignores Delete after a failed Create. A partial
        # Create may therefore outlive a successful CFN waiter. Check its retained
        # ownership table before returning, and never auto-adopt/delete an orphan.
        tables = {
            r["PhysicalResourceId"]
            for r in self.ledger.get("resources", []) + self.ledger.get("retained_candidates", [])
            if r["ResourceType"] == "AWS::DynamoDB::Table"
            and r["LogicalResourceId"].startswith("JiraProviderOwnership")
            and r.get("PhysicalResourceId")
        }
        self.ledger["jira_cleanup"] = []
        for table in tables:
            rows = self.aws(
                "dynamodb", "scan", "--table-name", table, "--consistent-read", "--limit", "100", "--no-paginate"
            )
            if rows.get("LastEvaluatedKey"):
                raise RuntimeError("Incomplete Jira ownership scan; cleanup remains unconfirmed")
            for row in rows.get("Items", []):
                if row.get("StackId", {}).get("S") != self.ledger["stack_id"]:
                    raise ValueError("Jira ownership record belongs to another stack")
                name = row["ProviderName"]["S"]
                absent = False
                try:
                    self.aws("bedrock-agentcore-control", "get-oauth2-credential-provider", "--name", name)
                except RuntimeError as error:
                    if "(ResourceNotFoundException) when calling the GetOauth2CredentialProvider operation" not in str(
                        error
                    ):
                        raise
                    absent = True
                self.ledger["jira_cleanup"].append({"provider_name": name, "absent": absent})
                self.checkpoint()
                if not absent:
                    raise RuntimeError(
                        "Stack deleted but Jira provider remains; retain ownership evidence and investigate before claiming cleanup"
                    )

    def resource(self, resource_type, logical_fragment=""):
        self.owned()
        found = [
            r["PhysicalResourceId"]
            for r in self.resources()
            if r["ResourceType"] == resource_type
            and logical_fragment in r["LogicalResourceId"]
            and r.get("PhysicalResourceId")
        ]
        if len(found) != 1:
            raise ValueError("Expected one owned resource of the requested type")
        return found[0]

    def evaluation_config(self):
        physical = self.resource("AWS::BedrockAgentCore::OnlineEvaluationConfig")
        config = self.aws(
            "bedrock-agentcore-control",
            "get-online-evaluation-config",
            "--online-evaluation-config-id",
            physical.split("/")[-1],
        )
        if physical not in (config["onlineEvaluationConfigId"], config["onlineEvaluationConfigArn"]):
            raise ValueError("Evaluation configuration ownership mismatch")
        if config["status"] != "ACTIVE" or config["executionStatus"] != "ENABLED":
            raise ValueError("Evaluation is not active/enabled")
        return config

    def output_resource(self, resource_type, output_fragment):
        stack = self.owned()
        values = [o["OutputValue"] for o in stack.get("Outputs", []) if output_fragment in o["OutputKey"]]
        resources = self.resources()
        if len(values) != 1 or not any(
            r["ResourceType"] == resource_type and r.get("PhysicalResourceId") == values[0] for r in resources
        ):
            raise ValueError("Output is ambiguous or not a resource in the recorded stack")
        return values[0]

    def publish(self, filename):
        topic = self.output_resource("AWS::SNS::Topic", "TicketsTopicArn")
        attrs = self.aws("sns", "get-topic-attributes", "--topic-arn", topic)["Attributes"]
        if attrs.get("Owner") != self.binding["account"] or attrs.get("TopicArn") != topic:
            raise ValueError("Actual SNS topic ownership mismatch")
        ticket = json.loads(Path(filename).read_text())
        if not isinstance(ticket, dict):
            raise ValueError("Ticket must be a JSON object")  # noqa: TRY004 -- invalid JSON file content
        self.aws("sns", "publish", "--topic-arn", topic, "--message", json.dumps(ticket))
        print("Synthetic ticket published to the verified stack topic")


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["deploy", "destroy", "publish", "inspect", "rotate-jira", "show"])
    for name in ("project", "target", "account", "region"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--ticket", default=str(ROOT / "seed-data/sample_ticket.json"))
    p.add_argument("--confirm-stack-id")
    p.add_argument("--secret-version-id")
    p.add_argument("--ticket-id")
    return p


def main():
    a = parser().parse_args()
    # Serialize wrappers in this project, not a machine-wide/cloud ownership lock.
    with (ROOT / ".sample-operation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        d = Deployment(ROOT, a.project, a.target, a.account, a.region)
        if a.command == "deploy":
            d.fresh()
        elif a.command == "destroy":
            d.destroy(a.confirm_stack_id)
        elif a.command == "publish":
            d.publish(a.ticket)
        elif a.command == "rotate-jira":
            d.rotate_jira(a.secret_version_id, a.confirm_stack_id)
        elif a.command == "show":
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", a.ticket_id or ""):
                raise ValueError("Specify an opaque --ticket-id")
            table = d.output_resource("AWS::DynamoDB::Table", "TicketsTableName")
            print(
                json.dumps(
                    d.aws(
                        "dynamodb",
                        "get-item",
                        "--table-name",
                        table,
                        "--consistent-read",
                        "--key",
                        json.dumps({"ticket_id": {"S": a.ticket_id}}),
                    ),
                    indent=2,
                )
            )
        else:
            print(json.dumps(d.owned(require_deployed=False), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        sys.exit(str(error))
