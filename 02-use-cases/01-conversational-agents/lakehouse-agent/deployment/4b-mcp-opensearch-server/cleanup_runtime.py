#!/usr/bin/env python3
"""
Cleanup OpenSearch MCP Server Runtime

Deletes:
- AgentCore OpenSearch MCP Server Runtime
- IAM execution role (AgentCoreRuntimeRole-opensearch-mcp)
- ECR repository (bedrock-agentcore-opensearch_mcp_server)
- CodeBuild project and its own SDK CodeBuild role (ownership verified via serviceRole)
- Local .bedrock_agentcore.yaml config
- SSM parameters

Usage:
    python cleanup_runtime.py [--keep-ssm]
"""

import argparse
import os
import sys
import time
from pathlib import Path

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.aws_session_utils import get_aws_session


class OpenSearchMCPRuntimeCleanup:
    def __init__(self, keep_ssm=False):
        _session, self.region, self.account_id = get_aws_session()
        self.bedrock = boto3.client("bedrock-agentcore-control", region_name=self.region)
        self.iam = boto3.client("iam")
        self.ecr = boto3.client("ecr", region_name=self.region)
        self.codebuild = boto3.client("codebuild", region_name=self.region)
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.keep_ssm = keep_ssm
        self.warnings = []

    def _get_ssm_param(self, name, default=None):
        try:
            return self.ssm.get_parameter(Name=f"/app/lakehouse-agent/{name}")["Parameter"]["Value"]
        except Exception:
            return default

    def _warn_unresolved_service_role(self, project_label, reason=None):
        """Loudly decline to delete an SDK CodeBuild role we cannot prove we own.

        Orphaning a role is strictly safer than deleting one belonging to another
        AgentCore project, but a silent skip hides the leak. Name every candidate
        declined and why, and repeat it in the final summary.
        """
        candidates = []
        try:
            paginator = self.iam.get_paginator("list_roles")
            for page in paginator.paginate(PathPrefix="/"):
                candidates += [
                    r["RoleName"]
                    for r in page["Roles"]
                    if r["RoleName"].startswith("AmazonBedrockAgentCoreSDKCodeBuild-")
                ]
        except Exception as e:
            print(f"   ⚠️  Could not list candidate CodeBuild roles: {e}")

        why = f"serviceRole lookup failed ({reason})" if reason else "the project reported no serviceRole"
        print(f"   ⚠️  WARNING: cannot verify CodeBuild role ownership for {project_label} — {why}.")
        print("      No CodeBuild role was deleted: deleting an unowned role would break another project.")
        if candidates:
            print(f"      Declined to delete {len(candidates)} prefix-matching role(s):")
            for name in candidates:
                print(f"        • {name}")
            print("      Delete manually only after confirming ownership in the CodeBuild console.")
        else:
            print("      No AmazonBedrockAgentCoreSDKCodeBuild- roles exist, so nothing was orphaned.")
        self.warnings.append(
            f"CodeBuild SDK role NOT deleted for {project_label} — {why}; "
            f"{len(candidates)} candidate role(s) left in place"
        )

    def _delete_codebuild_roles(self, service_role_names):
        """Delete only the SDK role(s) this runtime's own project(s) referenced.

        Every prefix match is examined (no early return, so a sibling runtime's role
        is never silently skipped), but a role is deleted only when it is one ours.
        """
        try:
            deleted = 0
            paginator = self.iam.get_paginator("list_roles")
            for page in paginator.paginate(PathPrefix="/"):
                for role in page["Roles"]:
                    role_name = role["RoleName"]
                    if not role_name.startswith("AmazonBedrockAgentCoreSDKCodeBuild-"):
                        continue
                    if role_name not in service_role_names:
                        print(f"   ⏭️  Skipping CodeBuild role owned by another project: {role_name}")
                        continue
                    for p in self.iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
                        self.iam.delete_role_policy(RoleName=role_name, PolicyName=p)
                    for p in self.iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]:
                        self.iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
                    self.iam.delete_role(RoleName=role_name)
                    print(f"   ✅ Deleted CodeBuild role: {role_name}")
                    deleted += 1
            if deleted == 0:
                print(f"   ⏭️  CodeBuild role not found: {', '.join(sorted(service_role_names))}")
        except Exception as e:
            print(f"   ⚠️  Error cleaning CodeBuild role: {e}")

    def delete_runtime(self):
        print("\n🗑️  Deleting OpenSearch MCP Server Runtime...")
        runtime_id = self._get_ssm_param("opensearch-mcp-runtime-id")
        if not runtime_id:
            print("   ⏭️  No runtime ID found in SSM")
            return
        try:
            self.bedrock.delete_agent_runtime(agentRuntimeId=runtime_id)
            print(f"   ✅ Deleted runtime: {runtime_id}")
            print("   ⏳ Waiting for deletion...")
            time.sleep(10)
        except self.bedrock.exceptions.ResourceNotFoundException:
            print(f"   ⏭️  Runtime not found: {runtime_id}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

    def delete_iam_role(self):
        print("\n🗑️  Deleting IAM execution role...")
        role_name = "AgentCoreRuntimeRole-opensearch-mcp"
        try:
            self.iam.get_role(RoleName=role_name)
        except self.iam.exceptions.NoSuchEntityException:
            print(f"   ⏭️  Role not found: {role_name}")
            return
        try:
            for p in self.iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
                self.iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            for p in self.iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]:
                self.iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
            self.iam.delete_role(RoleName=role_name)
            print(f"   ✅ Deleted role: {role_name}")
        except Exception as e:
            print(f"   ❌ Error: {e}")

    def delete_ecr_repository(self):
        print("\n🗑️  Deleting ECR repository...")
        repo_names = [
            "bedrock-agentcore-opensearch_mcp_server",
        ]
        for repo_name in repo_names:
            try:
                self.ecr.delete_repository(repositoryName=repo_name, force=True)
                print(f"   ✅ Deleted ECR repo: {repo_name}")
            except self.ecr.exceptions.RepositoryNotFoundException:
                pass
            except Exception as e:
                print(f"   ❌ Error deleting {repo_name}: {e}")

    def delete_codebuild_project(self):
        print("\n🗑️  Deleting CodeBuild project...")
        project_names = [
            "bedrock-agentcore-opensearch_mcp_server-builder",
        ]

        # Resolve each project's serviceRole BEFORE deleting it — the serviceRole is
        # the only reliable ownership signal for the toolkit-created CodeBuild role.
        # The "AmazonBedrockAgentCoreSDKCodeBuild-" prefix is shared by EVERY
        # AgentCore project in the account, so a prefix match alone can hit a role
        # owned by an unrelated runtime.
        service_role_names = set()
        found = []
        resolve_error = None
        try:
            projects = self.codebuild.batch_get_projects(names=project_names)["projects"]
            for project in projects:
                found.append(project["name"])
                role_name = project.get("serviceRole", "").split("/")[-1]
                if role_name:
                    service_role_names.add(role_name)
        except Exception as e:
            resolve_error = e
            print(f"   ⚠️  Could not resolve CodeBuild service role: {e}")

        for name in project_names:
            try:
                self.codebuild.delete_project(name=name)
                print(f"   ✅ Deleted CodeBuild project: {name}")
            except Exception:
                pass

        if not service_role_names:
            if found or resolve_error:
                self._warn_unresolved_service_role(", ".join(found) or ", ".join(project_names), resolve_error)
            else:
                print("   ⏭️  No CodeBuild project found — no SDK role to resolve")
            return
        self._delete_codebuild_roles(service_role_names)

    def delete_local_config(self):
        print("\n🗑️  Deleting local config files...")
        config_dir = Path(__file__).parent
        for f in [".bedrock_agentcore.yaml", ".bedrock_agentcore.yaml.bk", ".dockerignore", "Dockerfile"]:
            path = config_dir / f
            if path.exists():
                path.unlink()
                print(f"   ✅ Deleted: {f}")

    def delete_ssm_parameters(self):
        if self.keep_ssm:
            print("\n⏭️  Keeping SSM parameters (--keep-ssm)")
            return
        print("\n🗑️  Deleting SSM parameters...")
        params = ["opensearch-mcp-runtime-arn", "opensearch-mcp-runtime-id"]
        for p in params:
            try:
                self.ssm.delete_parameter(Name=f"/app/lakehouse-agent/{p}")
                print(f"   ✅ Deleted: /app/lakehouse-agent/{p}")
            except self.ssm.exceptions.ParameterNotFound:
                pass
            except Exception as e:
                print(f"   ❌ Error: {e}")

    def run(self):
        print("\n🧹 OpenSearch MCP Server Runtime Cleanup")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")
        self.delete_runtime()
        self.delete_iam_role()
        self.delete_ecr_repository()
        self.delete_codebuild_project()
        self.delete_local_config()
        self.delete_ssm_parameters()
        if self.warnings:
            print("\n⚠️  OpenSearch MCP Server Runtime cleanup complete WITH WARNINGS — manual follow-up needed:")
            for w in self.warnings:
                print(f"   • {w}")
        else:
            print("\n✨ OpenSearch MCP Server Runtime cleanup complete!")


def main():
    parser = argparse.ArgumentParser(description="Cleanup OpenSearch MCP Server Runtime")
    parser.add_argument("--keep-ssm", action="store_true", help="Keep SSM parameters")
    args = parser.parse_args()
    OpenSearchMCPRuntimeCleanup(keep_ssm=args.keep_ssm).run()


if __name__ == "__main__":
    main()
