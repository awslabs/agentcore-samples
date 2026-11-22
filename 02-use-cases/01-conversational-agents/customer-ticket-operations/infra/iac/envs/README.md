# Environments

One `.tfvars` + one `.tfbackend` per environment. Roots consume both via
`-var-file` and `-backend-config` flags (wired in the root `Makefile`).

## A new account

Nothing to edit: the Makefile derives the account ID and the state bucket from `PROFILE`, and
`make deploy-all PROFILE=<aws-profile>` creates the state backend on first use.

## Keep an existing Bedrock invocation-log destination

By default the observability root creates a CloudWatch log group and sets the account-wide
Bedrock model invocation logging configuration for the Region to point at it. If the account
already has invocation logging that you want to keep, set `existing_invocation_log_group` on
the observability root:

```hcl
existing_invocation_log_group = "/aws/bedrock/example_existing_invocations"
```

Put this in a copy of `demo.tfvars` and pass it with
`make deploy-all PROFILE=<aws-profile> TFVARS=/path/to/copy.tfvars`.
The root then leaves the existing logging configuration, log group and IAM role alone and only
records the group name in SSM for the app to read. Check first that invocation logging really
targets that group. CloudWatch Transaction Search (the X-Ray destination and 100% indexing) is
still turned on for the account and Region.

Set this before the first deploy. Switching an already deployed observability root to this mode
plans the deletion of the logging configuration it created.

## State layout

One S3 object per root:

```
s3://ticketops-demo-tf-state-<account>-us-east-1/demo/<root-relative-path>/terraform.tfstate
```
