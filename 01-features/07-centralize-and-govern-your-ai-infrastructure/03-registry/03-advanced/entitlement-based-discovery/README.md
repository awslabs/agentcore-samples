# Entitlement-based discovery in AWS Agent Registry

Use [`ebd_sample.py`](ebd_sample.py) to create a registry, publish sample MCP records, and verify what each caller can discover. Run either walkthrough independently.

- [IAM role tags](#iam-role-tags) shows what production, development, and untagged roles can discover and how approval changes the production catalog.
- [JWT claims](#jwt-claims) shows how business access, compliance, and sensitivity claims shape each caller's catalog.

## Prerequisites

Use Python 3.11 or later, the current AWS SDK for Python (Boto3), and an AWS account and Region with entitlement-based discovery. Set `AWS_REGION` and replace `publisher` in the commands with your publishing AWS CLI profile. This profile needs Registry and record management permissions, including `agent-registry:PutRecordVisibilityConfiguration`, and permission to approve records. If a separate profile handles approvals, add `--curator-profile CURATOR_PROFILE` to `setup` and `promote`.

```bash
export AWS_REGION=us-east-1
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade -r requirements.txt
```

## IAM role tags

**1. Create the registry and publish records.**

```bash
python ebd_sample.py --state iam-state.json setup --scenario iam \
  --name PlatformTools --owner-profile publisher
```

**2. Set up discovery-only roles.** The sample can create three roles tagged `environment=prod`, `environment=dev`, or untagged. Supply the IAM role or user ARN that will assume them. That principal needs `sts:AssumeRole` on the three roles, and the publisher needs permission to create and manage them.

```bash
python ebd_sample.py --state iam-state.json create-roles --owner-profile publisher \
  --prefix YOUR_UNIQUE_PREFIX --trusted-arn "$TRUSTED_IAM_ARN"
```

Set `PROD_ROLE_ARN` to the production role ARN printed by `create-roles` and `SOURCE_PROFILE` to the profile for the trusted principal. Configure the production profile, then repeat with the corresponding ARNs for `ebd-dev` and `ebd-untagged`.

```bash
aws configure set role_arn "$PROD_ROLE_ARN" --profile ebd-prod
aws configure set source_profile "$SOURCE_PROFILE" --profile ebd-prod
```

If you bring your own roles, grant `agent-registry:ListDiscoverableRegistryRecords` and `agent-registry:SearchDiscoverableRegistryRecords` on the registry ARN and `agent-registry:GetDiscoverableRegistryRecord` on its record ARNs.

**3. Verify discovery.**

```bash
python ebd_sample.py --state iam-state.json verify --caller prod --profile ebd-prod
python ebd_sample.py --state iam-state.json verify --caller dev --profile ebd-dev
python ebd_sample.py --state iam-state.json verify --caller untagged --profile ebd-untagged
```

Production sees payments and search. Development also sees the preview code assistant. The untagged role sees an empty catalog. The visibility configuration hides the data export record while its metadata marks it as under review.

**4. Promote the preview record and verify again.** The command updates the code assistant's metadata and approves a new revision. Production can now discover it.

```bash
python ebd_sample.py --state iam-state.json promote --owner-profile publisher
python ebd_sample.py --state iam-state.json verify --caller prod --profile ebd-prod
```

**5. Clean up.** This deletes the sample records, registry, and any roles created with `create-roles`.

```bash
python ebd_sample.py --state iam-state.json cleanup --owner-profile publisher
```

## JWT claims

**1. Set up the token issuer.** Use Cognito or an existing OIDC issuer that can issue access tokens with these claims.

| Caller | `data_access` | `compliance` | `sensitivity` |
| --- | --- | --- | --- |
| `finance` | `["financial", "engineering"]` | `["SOX"]` | `high` |
| `engineering` | `["engineering"]` | `["none"]` | `medium` |
| `hr-case` | `["PII"]` | `["HIPAA"]` | `medium` |
| `hr-audit` | `["PII"]` | `["SOX", "HIPAA"]` | `medium` |

Engineering uses `"none"` so the caller's compliance reference resolves. It can discover eligible engineering records with no compliance label.

For Cognito, create an Essentials or Plus user pool with a domain and a resource server with a custom scope. Create one confidential app client per caller and enable client credentials with that scope. Deploy [`cognito_claims.py`](cognito_claims.py) as a Python Lambda function with handler `cognito_claims.lambda_handler`. Set `EBD_CLIENT_CALLERS` on the function to a JSON object mapping each app client ID to its caller name. Grant the pool permission to invoke the function and attach it as a `V3_0` pre token generation trigger.

Set `OIDC_DISCOVERY_URL` to your issuer's OpenID configuration URL and set the four client ID variables below. For Cognito, set `TOKEN_ENDPOINT` to the pool domain's `/oauth2/token` URL and `DISCOVERY_SCOPE` to the full custom scope, such as `ebd-registry/discover`.

**2. Create the registry and publish records.**

```bash
python ebd_sample.py --state jwt-state.json setup --scenario jwt \
  --name BusinessTools --owner-profile publisher \
  --discovery-url "$OIDC_DISCOVERY_URL" \
  --allowed-client "$FINANCE_CLIENT_ID" --allowed-client "$ENGINEERING_CLIENT_ID" \
  --allowed-client "$HR_CASE_CLIENT_ID" --allowed-client "$HR_AUDIT_CLIENT_ID"
```

**3. Compare the HR catalogs.** Run this example for `hr-case`, then repeat with `HR_AUDIT_CLIENT_ID` and `--caller hr-audit`. `curl` prompts for the app client secret.

```bash
TOKEN=$(curl --silent --show-error --fail --user "$HR_CASE_CLIENT_ID" \
  --data 'grant_type=client_credentials' --data-urlencode "scope=$DISCOVERY_SCOPE" \
  "$TOKEN_ENDPOINT" | python -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')
EBD_ACCESS_TOKEN="$TOKEN" python ebd_sample.py --state jwt-state.json verify --caller hr-case
unset TOKEN
```

Both HR callers discover benefits enrollment and IT helpdesk. HR audit also discovers benefits and payroll sync. Payroll processing remains hidden from both callers because it requires high sensitivity. You can use `--caller finance` and `--caller engineering` with their respective access tokens.

**4. Clean up.** Delete the sample records and registry, then remove any Cognito and Lambda resources you created for this walkthrough.

```bash
python ebd_sample.py --state jwt-state.json cleanup --owner-profile publisher
```

In both walkthroughs, `setup` attaches the record visibility configuration with `UpdateRegistry` and revision `NO_CONFIG`. `verify` checks the complete List and BatchGet results, that hidden IDs return `RESOURCE_NOT_FOUND`, and that semantic search returns only eligible records. Keep the state file until cleanup completes.
