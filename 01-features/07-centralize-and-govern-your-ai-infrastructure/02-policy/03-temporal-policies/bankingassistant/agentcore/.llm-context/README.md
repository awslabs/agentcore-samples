# Schema guidance

This sample locks AgentCore CLI and its CDK library to 1.2.0, with agentcore.json version 2.
The old generated v0 type snapshots have been removed: do not use them to edit
v1 configuration. Use the installed @aws/agentcore-cdk project reader and
transformer, the v1 CLI command help, and the regression tests in cdk/test/.

Runtime v1 JSON uses `customJwtAuthorizer`; the Gateway boto3 API used by
setup.py uses `customJWTAuthorizer`. These are different interfaces. Validate
populated generated config as well as the intentionally empty checked-in spec.
Schema validation does not prove AWS authorization, deployment or temporal
policy enforcement. See ../../AGENTS.md and ../../README.md.
