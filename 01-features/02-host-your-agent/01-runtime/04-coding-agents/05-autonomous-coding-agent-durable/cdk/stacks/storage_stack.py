"""Storage stack: S3 bucket, S3 Files filesystem, mount targets, access point.

Mirrors deploy/05_s3files.sh. S3 Files resource types ARE natively supported in
CloudFormation (AWS::S3Files::FileSystem | MountTarget | AccessPoint, added
2026-04), so no custom resource is needed — we use raw CfnResource against the
verified CFN property shapes.

Layout matching the shell scripts:
  - ONE file system over the WHOLE bucket (no Prefix); the /work boundary lives in
    the access point's RootDirectory.Path, exactly like deploy/30_create_base_runtimes.sh.
  - ONE broad access point rootDir=/work, posix uid/gid 1000, mounted at /mnt/shared
    by every runtime.
  - 2 mount targets (one per private subnet / AZ).

Seed objects: the demo ticket JSONs are seeded via a BucketDeployment. The sample source
repo is NOT pre-seeded or vendored — the hydrate step git-clones the ticket's `repo_url`
directly into the work dir inside the sandbox on demand.
"""
import json
import os
from typing import List

import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
)
from constructs import Construct


class StorageStack(cdk.Stack):
    """Versioned S3 bucket + S3 Files filesystem with mount targets and access point."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str,
        vpc: ec2.IVpc,
        security_group: ec2.ISecurityGroup,
        private_subnets: List[ec2.ISubnet],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        account = cdk.Stack.of(self).account
        region = cdk.Stack.of(self).region

        # --- S3 Bucket (versioned — REQUIRED by S3 Files — block public, RETAIN) ---
        # Name MUST match what the runtime/orchestrator policies + memory namespace
        # expect: <project>-shared-<account>-<region> (see deploy/config.env BUCKET).
        self.bucket = s3.Bucket(
            self,
            "SharedBucket",
            bucket_name=f"{project}-shared-{account}-{region}",
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )

        # --- Seed the demo ticket sources (stand-in for JIRA/Atlassian MCP) ---
        seed_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets",
            "tickets-source",
        )
        os.makedirs(seed_dir, exist_ok=True)
        with open(os.path.join(seed_dir, "TICKET-1.json"), "w") as f:
            f.write(json.dumps({
                "id": "TICKET-1",
                "title": "Add a greeting module",
                "description": (
                    "Create greet.py exposing greet(name) returning \"Hello, <name>!\" "
                    "and a pytest test. Install pytest in the sandbox and make the test pass."
                ),
            }))
        with open(os.path.join(seed_dir, "RAINBOW-1.json"), "w") as f:
            f.write(json.dumps({
                "id": "RAINBOW-1", "repo": "rainbow", "runtime": "swift",
                "title": "Add YAML theme support",
                "description": (
                    "In this existing Swift package (Rainbow), add a Theme feature. Add the "
                    "Yams package (https://github.com/example-org/example-repo.git) as a SwiftPM dependency in "
                    "Package.swift. Add Sources/Theme.swift defining a `Theme` type with a static "
                    "`load(yaml:)` that parses a YAML mapping of role->color-name (e.g. "
                    "\"error: red\") using Yams, and a `String.applyingTheme(_:role:)` method that "
                    "applies the named color for that role using Rainbow's existing color API. Add "
                    "Tests/RainbowTests/ThemeTests.swift with XCTest cases covering load + "
                    "applyingTheme. Done when `swift build` succeeds and "
                    "`swift test --enable-test-discovery` passes including the new tests. Note: "
                    "this repo has a legacy LinuxMain.swift; use --enable-test-discovery."
                ),
            }))

        # --- Demo "Fire" templates (EXAMPLE tickets so the demo works on a fresh deploy) ---
        # These two _template-*.json files are what the demo frontend's "Fire" buttons
        # actually run: serve.py reads the template from S3, stamps a fresh ticket id per
        # click, and fires it. ALL ticket content (repo_url, title, description) lives here —
        # serve.py holds none of it. They carry repo_url so the hydrate step git-clones the
        # real Rainbow repo on demand (nothing is vendored/pre-seeded). This is purely EXAMPLE
        # demo data; replace with your own tickets for a real workload. Kept identical to
        # deploy/05_s3files.sh so a pure `cdk deploy` is demo-ready with no shell step.
        with open(os.path.join(seed_dir, "_template-feature.json"), "w") as f:
            f.write(json.dumps({
                "repo": "rainbow", "runtime": "swift",
                "repo_url": "https://github.com/example-org/example-repo.git",
                "title": "Add YAML theme support",
                "description": (
                    "In this existing Swift package (Rainbow), add a Theme feature using the "
                    "Yams SwiftPM dependency. Add Sources/Theme.swift with a Theme type that "
                    "loads a YAML role->color mapping, and String.applyingTheme(_:role:). Add "
                    "Tests/RainbowTests/ThemeTests.swift. Done when swift build succeeds and "
                    "swift test --enable-test-discovery passes. Use --enable-test-discovery "
                    "(legacy LinuxMain.swift present)."
                ),
            }))
        with open(os.path.join(seed_dir, "_template-memory.json"), "w") as f:
            f.write(json.dumps({
                "repo": "rainbow", "runtime": "swift",
                "repo_url": "https://github.com/example-org/example-repo.git",
                "title": "Add theme lookup helper",
                "description": (
                    "In this existing Swift package (Rainbow), extend the Theme type "
                    "(Sources/Theme.swift, which uses Yams). Add a method "
                    "Theme.colorName(for role: String) -> String? returning the configured "
                    "color name for a role, or nil. Add XCTest cases in "
                    "Tests/RainbowTests/ThemeTests.swift. Done when swift build succeeds and "
                    "swift test --enable-test-discovery passes. Use --enable-test-discovery."
                ),
            }))
        s3deploy.BucketDeployment(
            self,
            "SeedTickets",
            sources=[s3deploy.Source.asset(seed_dir)],
            destination_bucket=self.bucket,
            destination_key_prefix="tickets-source/",
            prune=False,
            retain_on_delete=True,
        )

        # --- Sync Role (assumed by elasticfilesystem.amazonaws.com) ---
        # Mirrors deploy/05_s3files.sh: the role S3 Files uses to sync bucket<->FS.
        self.sync_role = iam.Role(
            self,
            "SyncRole",
            role_name=f"{project}-s3files-sync",
            assumed_by=iam.ServicePrincipal(
                "elasticfilesystem.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:s3files:{region}:{account}:file-system/*"
                    },
                },
            ),
        )
        self.sync_role.add_to_policy(
            iam.PolicyStatement(
                sid="Bucket",
                actions=["s3:ListBucket*"],
                resources=[self.bucket.bucket_arn],
                conditions={"StringEquals": {"aws:ResourceAccount": account}},
            )
        )
        self.sync_role.add_to_policy(
            iam.PolicyStatement(
                sid="Objects",
                actions=[
                    "s3:AbortMultipartUpload",
                    "s3:DeleteObject*",
                    "s3:GetObject*",
                    "s3:List*",
                    "s3:PutObject*",
                ],
                resources=[f"{self.bucket.bucket_arn}/*"],
                conditions={"StringEquals": {"aws:ResourceAccount": account}},
            )
        )
        self.sync_role.add_to_policy(
            iam.PolicyStatement(
                sid="EventBridgeManage",
                actions=[
                    "events:DeleteRule", "events:DisableRule", "events:EnableRule",
                    "events:PutRule", "events:PutTargets", "events:RemoveTargets",
                ],
                resources=["arn:aws:events:*:*:rule/DO-NOT-DELETE-S3-Files*"],
                conditions={
                    "StringEquals": {"events:ManagedBy": "elasticfilesystem.amazonaws.com"}
                },
            )
        )
        self.sync_role.add_to_policy(
            iam.PolicyStatement(
                sid="EventBridgeRead",
                actions=[
                    "events:DescribeRule", "events:ListRuleNamesByTarget",
                    "events:ListRules", "events:ListTargetsByRule",
                ],
                resources=["arn:aws:events:*:*:rule/*"],
            )
        )

        # --- S3 Files FileSystem (native AWS::S3Files::FileSystem) ---
        # FS over the whole bucket; AcceptBucketWarning acknowledges sharing a
        # general-purpose bucket with S3 Files (matches acceptBucketWarning=True
        # in deploy/s3files_boto.py).
        self.file_system = cdk.CfnResource(
            self,
            "S3FilesFileSystem",
            type="AWS::S3Files::FileSystem",
            properties={
                "Bucket": self.bucket.bucket_arn,
                "RoleArn": self.sync_role.role_arn,
                "AcceptBucketWarning": True,
                "Tags": [{"Key": "Project", "Value": project}],
            },
        )
        self.file_system.apply_removal_policy(cdk.RemovalPolicy.RETAIN)
        # FS creation depends on the sync role's inline policy being in place.
        self.file_system.node.add_dependency(self.sync_role)

        # Ref returns the FS ARN; GetAtt FileSystemId returns fs-...; both are accepted
        # by the MountTarget/AccessPoint FileSystemId property (pattern allows arn|fs-).
        fs_id = cdk.Token.as_string(self.file_system.get_att("FileSystemId"))

        # --- Mount Targets (one per private subnet / AZ) ---
        self.mount_targets: List[cdk.CfnResource] = []
        for i, subnet in enumerate(private_subnets):
            mt = cdk.CfnResource(
                self,
                f"MountTarget{i}",
                type="AWS::S3Files::MountTarget",
                properties={
                    "FileSystemId": fs_id,
                    "SubnetId": subnet.subnet_id,
                    "SecurityGroups": [security_group.security_group_id],
                },
            )
            mt.add_dependency(self.file_system)
            self.mount_targets.append(mt)

        # --- Broad Access Point (rootDir=/work, uid/gid 1000) ---
        # Uid/Gid are STRINGS in CFN (pattern ^[0-9]+$). Mounted at /mnt/shared by
        # all runtimes; the /work root is the bucket-escape boundary.
        self.access_point = cdk.CfnResource(
            self,
            "S3FilesAccessPoint",
            type="AWS::S3Files::AccessPoint",
            properties={
                "FileSystemId": fs_id,
                "PosixUser": {"Uid": "1000", "Gid": "1000"},
                "RootDirectory": {
                    "Path": "/work",
                    "CreationPermissions": {
                        "OwnerUid": "1000",
                        "OwnerGid": "1000",
                        "Permissions": "0775",
                    },
                },
                "Tags": [{"Key": "Project", "Value": project}],
            },
        )
        for mt in self.mount_targets:
            self.access_point.add_dependency(mt)
        self.access_point.apply_removal_policy(cdk.RemovalPolicy.RETAIN)

        # --- Exports ---
        self.access_point_arn: str = cdk.Token.as_string(
            self.access_point.get_att("AccessPointArn")
        )
        self.fs_id: str = fs_id

        cdk.CfnOutput(
            self,
            "BucketName",
            value=self.bucket.bucket_name,
            export_name=f"{project}-bucket-name",
        )
        cdk.CfnOutput(
            self,
            "AccessPointArn",
            value=self.access_point_arn,
            export_name=f"{project}-access-point-arn",
        )
        cdk.CfnOutput(
            self,
            "FileSystemId",
            value=self.fs_id,
            export_name=f"{project}-fs-id",
        )
