# Identity setup: Okta, IAM Identity Center and the permission sets

Do parts 1 to 3 before the first `deploy`. Part 4 comes after deploy's first pass, because it needs the
CloudFront URL that deploy creates.

## What you end up with

| Where | What | Why |
|---|---|---|
| Okta | Group `devbox-users` | Who gets a dev box. The box's sign-in checks it. |
| Okta | Groups `ai-claude-power` and `ai-claude-standard` | The person's tier. Each person is in exactly one. |
| IAM Identity Center | Okta as the identity source, with users and the two tier groups synced from Okta | The AWS sign-in inside the box. |
| IAM Identity Center | Permission sets `ClaudeCode-Power` and `ClaudeCode-Standard`, assigned to the tier groups | Which Claude models on Amazon Bedrock each tier may call. |
| Okta | App "Dev Box" (OIDC, single-page app), with a `devbox` scope and claims | Signing in to the workbench and the box. |

A person signs in twice, as two hops:

1. **Okta → the box.** The browser signs in with the Dev Box app. The runtime's authorizer lets in only that
   person's token.
2. **IAM Identity Center → Bedrock.** Inside the box, `aws sso login` signs in with a device code and gets the
   `ClaudeCode-<tier>` role. Claude Code calls Bedrock with it. The box itself has no Bedrock access.

The group names are settings in `deploy/devbox.env` (`DEVBOX_OKTA_GROUP` and `DEVBOX_TIER_GROUPS`). The
permission set names are not: they must be exactly `ClaudeCode-Power` and `ClaudeCode-Standard` (see part 3).

## 1. Okta: the groups

In the Okta admin console, **Directory › Groups › Add group**:

- `devbox-users`
- `ai-claude-power`
- `ai-claude-standard`

Put each person in `devbox-users` and in exactly one tier group. Someone in no tier group, or in both, gets no
box, and the page tells them why.

IAM Identity Center's sync needs every Okta user to have a first name, last name, username and display name.

## 2. Connect Okta to IAM Identity Center

Follow AWS's tutorial, [Configure SAML and SCIM with Okta and IAM Identity Center](https://docs.aws.amazon.com/singlesignon/latest/userguide/gs-okta.html).
For this sample, what matters is:

1. **Enable IAM Identity Center** (an organization instance) if it isn't on yet. Note its Region: it goes into
   `IDC_REGION` in `devbox.env`.
2. **SAML.** In Okta, add the **AWS IAM Identity Center** app from the app catalog. In IAM Identity Center,
   **Settings › Change identity source › External identity provider**: upload the Okta app's IdP metadata, then
   copy IAM Identity Center's ACS URL and issuer URL back into the Okta app.
3. **SCIM.** In IAM Identity Center, enable automatic provisioning. Copy the SCIM endpoint and access token into
   the Okta app's **Provisioning** tab (the token is shown only once). Turn on creating, updating and
   deactivating users.
4. **Assign and push.** Assign people to the Okta app through `devbox-users`, then use **Push Groups** for
   `ai-claude-power` and `ai-claude-standard`. Okta doesn't support using the same group for both assignment and
   group push, which is why assignment goes through `devbox-users` and only the tier groups are pushed.
5. **Check.** In IAM Identity Center, **Groups** shows `ai-claude-power` and `ai-claude-standard` with their
   members.

The AWS access portal URL (`https://<identity store id>.awsapps.com/start`) is what the box signs in to. Leave
`IDC_START_URL` blank in `devbox.env` to use it, or set it if you use a custom one.

## 3. IAM Identity Center: the permission sets

Create the two permission sets in the account that holds IAM Identity Center (the management account), then
assign each one to its tier group on the account where the dev boxes run.

| | `ClaudeCode-Standard` | `ClaudeCode-Power` |
|---|---|---|
| Assigned to | `ai-claude-standard` | `ai-claude-power` |
| Claude models | Sonnet, Haiku | Opus, Sonnet, Haiku |
| Session duration | 8 hours | 8 hours |

The names must be exactly these:

- The box signs in with the role named after the person's tier.
- The web-search tools gateway lets in only roles named `AWSReservedSSO_ClaudeCode-*`.
- `uv run deploy/devbox.py check` reports a permission set that's missing.

### The inline policy

This policy is the hard limit on what each person can do with Claude Code. Here is `ClaudeCode-Standard`.
`ClaudeCode-Power` is the same, with two more resources for Opus:
`inference-profile/<GEO>.<OPUS_MODEL>` and `foundation-model/<OPUS_MODEL>`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeStandardModels",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:*:*:inference-profile/<GEO>.<SONNET_MODEL>",
        "arn:aws:bedrock:*:*:inference-profile/<GEO>.<HAIKU_MODEL>",
        "arn:aws:bedrock:*::foundation-model/<SONNET_MODEL>",
        "arn:aws:bedrock:*::foundation-model/<HAIKU_MODEL>"
      ]
    },
    {
      "Sid": "DiscoverProfiles",
      "Effect": "Allow",
      "Action": ["bedrock:ListInferenceProfiles", "bedrock:GetInferenceProfile"],
      "Resource": "*"
    },
    {
      "Sid": "SendClaudeCodeMetrics",
      "Effect": "Allow",
      "Action": "cloudwatch:PutMetricData",
      "Resource": "*"
    },
    {
      "Sid": "ReadOwnDirectoryRecordOnly",
      "Effect": "Allow",
      "Action": ["identitystore:GetUserId", "identitystore:DescribeUser"],
      "Resource": [
        "arn:aws:identitystore::<MANAGEMENT_ACCOUNT_ID>:identitystore/<IDENTITY_STORE_ID>",
        "arn:aws:identitystore:::user/${identitystore:userId}"
      ]
    }
  ]
}
```

| Statement | What it allows |
|---|---|
| `InvokeStandardModels` | Calling only the tier's models, through the cross-Region inference profile (`<GEO>.<model>`) or the model itself. Nothing else in Bedrock. |
| `DiscoverProfiles` | Looking up inference profiles, which Claude Code does at start-up. |
| `SendClaudeCodeMetrics` | Claude Code's usage metrics to CloudWatch. |
| `ReadOwnDirectoryRecordOnly` | Reading the person's own Identity Center user record, and no one else's. |

Use the same values as `deploy/devbox.env`: `GEO`, `OPUS_MODEL`, `SONNET_MODEL` and `HAIKU_MODEL`. The box's
Claude Code model list is built from them, so if they don't match the policy, Claude Code offers a model that IAM
refuses.

### With the AWS CLI

As an IAM Identity Center admin in the management account (the `ORG_ADMIN_PROFILE` from `devbox.env`), in the
IAM Identity Center Region. Shown for Standard; repeat with `Power`, `ai-claude-power` and the Power policy.

```bash
P="--profile org-admin --region <IDC_REGION>"
IDC=$(aws sso-admin list-instances $P --query 'Instances[0].InstanceArn' --output text)
STORE=$(aws sso-admin list-instances $P --query 'Instances[0].IdentityStoreId' --output text)

PS=$(aws sso-admin create-permission-set $P --instance-arn "$IDC" --name ClaudeCode-Standard \
  --description "Claude Code on Bedrock - Standard tier" --session-duration PT8H \
  --query PermissionSet.PermissionSetArn --output text)
aws sso-admin put-inline-policy-to-permission-set $P --instance-arn "$IDC" \
  --permission-set-arn "$PS" --inline-policy file://claude-code-standard.json

GROUP=$(aws identitystore get-group-id $P --identity-store-id "$STORE" --query GroupId --output text \
  --alternate-identifier '{"UniqueAttribute":{"AttributePath":"displayName","AttributeValue":"ai-claude-standard"}}')
aws sso-admin create-account-assignment $P --instance-arn "$IDC" --permission-set-arn "$PS" \
  --target-type AWS_ACCOUNT --target-id <DEV_BOX_ACCOUNT_ID> --principal-type GROUP --principal-id "$GROUP"
```

Creating the assignment provisions the permission set into the account. If you change the policy later, run
`aws sso-admin provision-permission-set` (or **Permission sets › Update in accounts** in the console) to push the
change.

## 4. After deploy's first pass: the Dev Box app in Okta

Run `uv run deploy/devbox.py deploy` once with `DEVBOX_OKTA_CLIENT_ID` blank. At the end it prints these steps
with your real CloudFront URL filled in:

1. **Applications › Create App Integration › OIDC › Single-Page Application**, named "Dev Box":
   - Grant types: Authorization Code and Refresh Token (rotate the token after every use).
   - Sign-in redirect URI `https://<workbench>/callback`, sign-out redirect URI `https://<workbench>/`.
   - Assignments: only `devbox-users`. DPoP off.
   - Copy its client ID into `devbox.env` as `DEVBOX_OKTA_CLIENT_ID`.
2. **Security › API › Trusted Origins**: add `https://<workbench>` for CORS and Redirect. Use exactly this
   origin, never a wildcard.
3. **Security › API › Authorization Servers › default**:
   - Add the scope `devbox`.
   - Add a claim `client_id` (Access Token, Expression `app.clientId`, in scope `devbox`).
   - Add a claim `groups` (Access Token, Groups, filter Matches regex
     `^(devbox\-users|ai\-claude\-power|ai\-claude\-standard)$`, in scope `devbox`).
   - Add an access policy "Dev Box" for the Dev Box client, at priority 1. Give it one rule:
     - group `devbox-users`;
     - Authorization Code and Refresh Token;
     - exactly the scopes `openid profile email offline_access devbox`;
     - access token 60 minutes, refresh token 12 hours, expiring after 2 hours unused.
   - No rule in this authorization server may allow "Any scopes".
4. **Token Preview** on the same authorization server, for a `devbox-users` member. Expect:
   - `scp` has `devbox`;
   - `groups` has `devbox-users` and their one tier group;
   - `client_id` is the Dev Box app, and `uid` is present.

   For someone not in `devbox-users`, the preview must be denied.

Then run `deploy` again. Sign-in now works, and each person's box is made on their first visit.

If you `undeploy` and deploy again, the workbench gets a new CloudFront URL. Update the two redirect URIs and the
Trusted Origin: deploy prints the old and new values.

## Check it

- `uv run deploy/devbox.py check` confirms that both permission sets exist and that the tier groups are in
  IAM Identity Center.
- Sign in as a Standard user. In the box, `aws sso login`, then `claude`: `/model` lists Sonnet and Haiku, and a
  request for Opus is refused.
- Move that person to `ai-claude-power` in Okta. After the next sync, at their next sign-in, Opus is available.
