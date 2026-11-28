"""Exercise the actual shell wrappers with journaled fake AWS/CLI executables.
These are dispatch/negative tests, NOT AWS success evidence.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "111111111111"
REGION = "us-west-2"
PROJECT = "UniqueIT"
STACK = f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/AgentCore-{PROJECT}-test/12345678-1234-1234-1234-123456789012"
FAKE = """#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]; root=pathlib.Path(os.environ['FIXTURE']); state=root/'remote.json'
remote=json.loads(state.read_text()); mode=os.environ.get('MODE','')
with (root/'commands.jsonl').open('a') as f: f.write(json.dumps([pathlib.Path(sys.argv[0]).name,*args])+'\\n')
def out(x):print(json.dumps(x));sys.exit(0)
def fail():print('offline simulated denial',file=sys.stderr);sys.exit(42)
def arg(k):return args[args.index(k)+1]
if pathlib.Path(sys.argv[0]).name=='agentcore':
 if args==['--version']:print('1.2.0');sys.exit(0)
 if args==['config','transactionSearch']:
  config=json.loads((pathlib.Path(os.environ['HOME'])/'.agentcore/config.json').read_text());assert config['transactionSearch'] is False
  print('false');sys.exit(0)
 assert args==['deploy','--target','test','--skip-bootstrap'],args
 if mode=='cli-fail':fail()
 p=root/'agentcore/.cli/deployed-state.json';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'targets':{'test':{'stackArn':remote['StackId']}}}))
 remote['StackStatus']='UPDATE_COMPLETE';state.write_text(json.dumps(remote));sys.exit(0)
assert '--region' in args and arg('--region')=='us-west-2'
op=args[:2]
if op==['sts','get-caller-identity']:out({'Account':'222222222222' if mode=='wrong-account' else '111111111111','Arn':'arn:aws:iam::111111111111:user/offline'})
if op==['cloudformation','list-stacks']:out({'StackSummaries':[{'StackName':remote['StackName']}] if mode=='remote-exists' else []})
if op==['cloudformation','create-stack']:
 assert json.loads(arg('--template-body'))=={'AWSTemplateFormatVersion':'2010-09-09','Resources':{'FreshReservation':{'Type':'AWS::SNS::Topic'}}}
 remote['Tags']=json.loads(arg('--tags'));remote['StackStatus']='CREATE_COMPLETE';state.write_text(json.dumps(remote));out({'StackId':remote['StackId']})
if op==['cloudformation','describe-stacks']:
 if mode=='wrong-stack':remote['StackId']=remote['StackId'].replace('111111111111','222222222222')
 if mode=='wrong-tags':remote['Tags']=[]
 out({'Stacks':[remote]})
if op==['cloudformation','list-stack-resources']:
 out({'StackResourceSummaries':remote['resources']})
if op==['cloudformation','describe-stack-events']:out({'StackEvents':remote.get('events',[])})
if op==['dynamodb','scan']:
 out({'Items':[{'StackId':{'S':remote['StackId']},'ProviderName':{'S':'owned-jira-provider'},'Status':{'S':'CREATING'}}]})
if op==['bedrock-agentcore-control','get-oauth2-credential-provider']:out({'name':'owned-jira-provider'})
if op==['cloudformation','update-stack']:
 assert '--use-previous-template' in args and '--template-body' not in args
 for p in json.loads(arg('--parameters')):
  if 'ParameterValue' in p:
   remote['Parameters']=[p if old['ParameterKey']==p['ParameterKey'] else old for old in remote['Parameters']]
 remote['StackStatus']='UPDATE_COMPLETE';state.write_text(json.dumps(remote));out({'StackId':remote['StackId']})
if op==['secretsmanager','describe-secret']:
 out({'ARN':arg('--secret-id'),'VersionIdsToStages':{'b'*32:['AWSCURRENT']}})
if op==['cloudformation','delete-stack']:
 assert arg('--stack-name')==remote['StackId'];remote['StackStatus']='DELETE_COMPLETE';state.write_text(json.dumps(remote));out({})
if op==['cloudformation','wait']:
 if mode=='waiter-fail':fail()
 out({})
if op==['sns','get-topic-attributes']:out({'Attributes':{'Owner':'111111111111','TopicArn':remote['topic']}})
if op==['sns','publish']:out({'MessageId':'offline-message'})
if op==['bedrock-agentcore-control','get-online-evaluation-config']:
 out({'onlineEvaluationConfigId':'TestEval-1234567890','onlineEvaluationConfigArn':'arn:aws:bedrock-agentcore:us-west-2:111111111111:online-evaluation-config/TestEval-1234567890','status':'ACTIVE','executionStatus':'ENABLED','evaluators':[{'evaluatorId':'Builtin.'+n} for n in ['Correctness','Helpfulness','ToolSelectionAccuracy','GoalSuccessRate']],'rule':{'samplingConfig':{'samplingPercentage':100}},'outputConfig':{'cloudWatchConfig':{'logGroupName':'/owned/evaluation'}}})
if op==['dynamodb','get-item']:out({'Item':{'ticket_id':json.loads(arg('--key'))['ticket_id'],'status':{'S':'Resolved'},'resolution_comment':{'S':'nonempty but no tools ran'}}})
raise AssertionError(args)
"""


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        shutil.copytree(ROOT / "scripts", self.root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "agentcore").mkdir()
        (self.root / "seed-data").mkdir()
        spec = json.loads((ROOT / "agentcore/agentcore.json").read_text())
        spec["name"] = PROJECT
        (self.root / "agentcore/agentcore.json").write_text(json.dumps(spec))
        (self.root / "agentcore/aws-targets.json").write_text(
            json.dumps([{"name": "test", "account": ACCOUNT, "region": REGION}])
        )
        (self.root / "seed-data/sample_ticket.json").write_text('{"ticket_id":"T-1","synthetic":true}')
        binary = self.root / "bin"
        binary.mkdir()
        (binary / "python3").write_text("#!/bin/sh\nexec " + sys.executable + ' "$@"\n')
        (binary / "python3").chmod(0o700)
        for name in ("aws", "agentcore"):
            (binary / name).write_text(FAKE)
            (binary / name).chmod(0o700)
        self.env = dict(
            os.environ,
            FIXTURE=str(self.root),
            MODE="",
            IT_SAMPLE_EVALUATION_CONTENT="synthetic-only",
            PATH=str(binary) + ":" + os.environ["PATH"],
            AGENTCORE_CLI=str(binary / "agentcore"),
        )
        topic = f"arn:aws:sns:{REGION}:{ACCOUNT}:owned-topic"
        resources = [
            {"ResourceType": typ, "LogicalResourceId": logical, "PhysicalResourceId": physical}
            for typ, logical, physical in [
                ("AWS::SNS::Topic", "InfraTicketsTopic", topic),
                ("AWS::DynamoDB::Table", "InfraTicketsTable", "OwnedTickets"),
                ("AWS::BedrockAgentCore::Memory", "Memory", "TestMemory-1234567890"),
                ("AWS::BedrockAgentCore::OnlineEvaluationConfig", "Eval", "TestEval-1234567890"),
            ]
        ]
        self.remote = {
            "StackId": STACK,
            "StackName": f"AgentCore-{PROJECT}-test",
            "StackStatus": "UPDATE_COMPLETE",
            "Tags": [
                {"Key": "agentcore:project-name", "Value": PROJECT},
                {"Key": "agentcore:target-name", "Value": "test"},
                {"Key": "it-sample:owner", "Value": "nonce"},
            ],
            "Outputs": [
                {"OutputKey": "InfraTicketsTopicArn", "OutputValue": topic},
                {"OutputKey": "InfraTicketsTableName", "OutputValue": "OwnedTickets"},
            ],
            "resources": resources,
            "topic": topic,
        }
        (self.root / "remote.json").write_text(json.dumps(self.remote))
        self.args = ["--project", PROJECT, "--target", "test", "--account", ACCOUNT, "--region", REGION]

    def deployed(self):
        ledger = {
            "version": 1,
            "binding": {
                "project": PROJECT,
                "target": "test",
                "account": ACCOUNT,
                "region": REGION,
                "stack_name": f"AgentCore-{PROJECT}-test",
            },
            "nonce": "nonce",
            "stack_id": STACK,
            "status": "DEPLOYED",
        }
        (self.root / ".sample-deployment.json").write_text(json.dumps(ledger))
        state = self.root / "agentcore/.cli/deployed-state.json"
        state.parent.mkdir()
        state.write_text(json.dumps({"targets": {"test": {"stackArn": STACK}}}))

    def call(self, name, extra=(), mode=""):
        return subprocess.run(
            ["bash", str(self.root / "scripts" / name), *self.args, *extra],
            cwd=self.root,
            env={**self.env, "MODE": mode},
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def commands(self):
        path = self.root / "commands.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_fresh_reserves_stack_before_cli_and_journals_identity(self):
        result = self.call("deploy.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.commands()
        create = next(i for i, cmd in enumerate(log) if cmd[1:3] == ["cloudformation", "create-stack"])
        deploy = next(i for i, cmd in enumerate(log) if cmd[:2] == ["agentcore", "deploy"])
        self.assertLess(create, deploy)
        self.assertEqual(json.loads((self.root / ".sample-deployment.json").read_text())["stack_id"], STACK)

    def test_fresh_rejects_matching_existing_state_and_remote_names(self):
        self.deployed()
        self.assertNotEqual(self.call("deploy.sh").returncode, 0)
        self.assertFalse(any(c[:2] == ["agentcore", "deploy"] for c in self.commands()))

    def test_cli_failure_keeps_proven_reservation_for_cleanup(self):
        result = self.call("deploy.sh", mode="cli-fail")
        self.assertNotEqual(result.returncode, 0)
        ledger = json.loads((self.root / ".sample-deployment.json").read_text())
        self.assertEqual(ledger["status"], "DEPLOY_FAILED")
        self.assertEqual(ledger["stack_id"], STACK)
        self.assertTrue(ledger["resources"])
        self.assertTrue(ledger["cli_started"])
        cleanup = self.call("destroy.sh", ["--confirm-stack-id", STACK])
        self.assertNotEqual(cleanup.returncode, 0)
        self.assertIn("CLI state missing", cleanup.stderr)
        self.assertFalse(any(c[1:3] == ["cloudformation", "delete-stack"] for c in self.commands()))

    def test_unknown_legacy_cli_state_is_not_ignored(self):
        state = self.root / "agentcore/.cli"
        state.mkdir()
        (state / "unknown-old-state.json").write_text("{}")
        self.assertNotEqual(self.call("deploy.sh").returncode, 0)
        self.assertFalse(any(c[1:3] == ["cloudformation", "create-stack"] for c in self.commands()))

    def test_remote_existing_is_not_adopted(self):
        for name in (f"AgentCore-{PROJECT}-test", f"AgentCore-{PROJECT}-other-target"):
            self.remote["StackName"] = name
            (self.root / "remote.json").write_text(json.dumps(self.remote))
            self.assertNotEqual(self.call("deploy.sh", mode="remote-exists").returncode, 0)
            self.assertFalse((self.root / ".sample-deployment.json").exists())

    def test_wrong_account_region_stack_tags_and_missing_state_reject_before_delete(self):
        self.deployed()
        for mode in ("wrong-account", "wrong-stack", "wrong-tags"):
            result = self.call("destroy.sh", ["--confirm-stack-id", STACK], mode)
            self.assertNotEqual(result.returncode, 0)
        self.env["AWS_REGION"] = "us-east-1"
        self.assertNotEqual(self.call("destroy.sh", ["--confirm-stack-id", STACK]).returncode, 0)
        self.env["AWS_REGION"] = REGION
        (self.root / "agentcore/.cli/deployed-state.json").unlink()  # fixture-only negative, never product recovery
        self.assertNotEqual(self.call("destroy.sh", ["--confirm-stack-id", STACK]).returncode, 0)
        self.assertFalse(any(c[1:3] == ["cloudformation", "delete-stack"] for c in self.commands()))

    def test_missing_ledger_and_renamed_project_reject(self):
        self.assertNotEqual(self.call("destroy.sh", ["--confirm-stack-id", STACK]).returncode, 0)
        self.deployed()
        specfile = self.root / "agentcore/agentcore.json"
        spec = json.loads(specfile.read_text())
        spec["name"] = "Renamed"
        specfile.write_text(json.dumps(spec))
        self.assertNotEqual(self.call("publish_ticket.sh").returncode, 0)

    def test_waiter_failure_retains_both_states(self):
        self.deployed()
        result = self.call("destroy.sh", ["--confirm-stack-id", STACK], "waiter-fail")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("deletion confirmed", result.stdout)
        self.assertTrue((self.root / "agentcore/.cli/deployed-state.json").exists())
        self.assertEqual(json.loads((self.root / ".sample-deployment.json").read_text())["status"], "DELETE_ATTEMPTED")

    def test_successful_stack_delete_still_retains_cleanup_ledger(self):
        self.deployed()
        result = self.call("destroy.sh", ["--confirm-stack-id", STACK])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "agentcore/.cli/deployed-state.json").exists())
        self.assertIn("cleanup is not complete", result.stdout)

    def test_failed_create_orphan_is_not_hidden_by_successful_stack_waiter(self):
        self.deployed()
        # The rolled-back table no longer appears in current resources, but the
        # CFN event ledger still identifies retained provider ownership evidence.
        self.remote["events"] = [
            {
                "StackId": STACK,
                "EventId": "owned-event",
                "ResourceType": "AWS::DynamoDB::Table",
                "LogicalResourceId": "JiraProviderOwnership123",
                "PhysicalResourceId": "OwnedJiraProof",
                "ResourceStatus": "DELETE_SKIPPED",
            }
        ]
        (self.root / "remote.json").write_text(json.dumps(self.remote))
        result = self.call("destroy.sh", ["--confirm-stack-id", STACK])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Jira provider remains", result.stderr)
        ledger = json.loads((self.root / ".sample-deployment.json").read_text())
        self.assertFalse(ledger["jira_cleanup"][0]["absent"])
        self.assertTrue(ledger["retained_candidates"])

    def test_publish_checks_actual_topic_ownership_and_renamed_target(self):
        self.deployed()
        result = self.call("publish_ticket.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(c[1:3] == ["sns", "get-topic-attributes"] for c in self.commands()))

    def test_secret_rotation_uses_previous_template_and_one_parameter(self):
        self.deployed()
        self.remote["Parameters"] = [
            {"ParameterKey": "JiraSecretVersionId", "ParameterValue": "a" * 32},
            {"ParameterKey": "BootstrapVersion", "ParameterValue": "123"},
        ]
        self.remote["Outputs"].append(
            {
                "OutputKey": "JiraExternalSecretArn",
                "OutputValue": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:external-jira-abcdef",
            }
        )
        (self.root / "remote.json").write_text(json.dumps(self.remote))
        result = subprocess.run(
            [
                sys.executable,
                str(self.root / "scripts/lifecycle.py"),
                "rotate-jira",
                *self.args,
                "--confirm-stack-id",
                STACK,
                "--secret-version-id",
                "b" * 32,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        update = next(c for c in self.commands() if c[1:3] == ["cloudformation", "update-stack"])
        self.assertIn("--use-previous-template", update)
        self.assertNotIn("--template-body", update)
        parameters = json.loads(update[update.index("--parameters") + 1])
        self.assertEqual(parameters[0], {"ParameterKey": "JiraSecretVersionId", "ParameterValue": "b" * 32})
        self.assertEqual(parameters[1], {"ParameterKey": "BootstrapVersion", "UsePreviousValue": True})
        self.remote["Outputs"][-1]["OutputValue"] = self.remote["Outputs"][-1]["OutputValue"].replace(
            ACCOUNT, "222222222222"
        )
        (self.root / "remote.json").write_text(json.dumps(self.remote))
        denied = subprocess.run(
            [
                sys.executable,
                str(self.root / "scripts/lifecycle.py"),
                "rotate-jira",
                *self.args,
                "--confirm-stack-id",
                STACK,
                "--secret-version-id",
                "b" * 32,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("secret account/region mismatch", denied.stderr)
        self.assertEqual(sum(c[1:3] == ["cloudformation", "update-stack"] for c in self.commands()), 1)

    def test_e2e_resolved_nonempty_without_tools_fails_actual_wrapper(self):
        self.deployed()
        result = self.call("test-e2e.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lacks workflow session evidence", result.stderr)


if __name__ == "__main__":
    unittest.main()
