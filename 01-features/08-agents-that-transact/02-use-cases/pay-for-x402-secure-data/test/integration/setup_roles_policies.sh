client_trust_policy() {
  jq -n --arg setupPrincipal "$TRUSTED_SETUP_PRINCIPAL_ARN" '{
    Version: "2012-10-17",
    Statement: [{
      Sid: "AllowSpecificSetupPrincipalAssume",
      Effect: "Allow",
      Principal: {AWS: $setupPrincipal},
      Action: "sts:AssumeRole"
    }]
  }'
}

service_trust_policy() {
  jq -n '{
    Version: "2012-10-17",
    Statement: [{
      Sid: "AllowAccessToBedrockAgentCore",
      Effect: "Allow",
      Principal: {Service: "bedrock-agentcore.amazonaws.com"},
      Action: "sts:AssumeRole"
    }]
  }'
}

process_payment_trust_policy() {
  jq -n \
    --arg setupPrincipal "$TRUSTED_SETUP_PRINCIPAL_ARN" \
    --arg accountId "$ACCOUNT_ID" \
    --arg region "$AWS_REGION" \
    '{
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "AllowSpecificSetupPrincipalAssume",
        Effect: "Allow",
        Principal: {AWS: $setupPrincipal},
        Action: "sts:AssumeRole"
      },
      {
        Sid: "AllowAgentCoreRuntimeAssume",
        Effect: "Allow",
        Principal: {Service: "bedrock-agentcore.amazonaws.com"},
        Action: "sts:AssumeRole",
        Condition: {
          StringEquals: {"aws:SourceAccount": $accountId},
          ArnLike: {"aws:SourceArn": ("arn:aws:bedrock-agentcore:" + $region + ":" + $accountId + ":runtime/*")}
        }
      }
    ]
  }'
}

# Resources are scoped to this account and to the AgentCore payments
# resource types (manager / connector / credential-provider), rather than
# "*". The manager segment stays wildcarded because the manager ID does not
# exist at role-creation time. Production hardening: once the Manager ID is
# stable, replace the "*" manager segment with the concrete
# "payment-manager/${MANAGER_ID}" ARN, or add a tag-based condition.
control_plane_policy() {
  jq -n --arg accountId "$ACCOUNT_ID" '{
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "AllowPaymentManagerOperations",
        Effect: "Allow",
        Action: [
          "bedrock-agentcore:CreatePaymentManager",
          "bedrock-agentcore:GetPaymentManager",
          "bedrock-agentcore:ListPaymentManagers",
          "bedrock-agentcore:DeletePaymentManager",
          "bedrock-agentcore:UpdatePaymentManager"
        ],
        Resource: [("arn:aws:bedrock-agentcore:*:" + $accountId + ":payment-manager/*")]
      },
      {
        Sid: "AllowPaymentConnectorOperations",
        Effect: "Allow",
        Action: [
          "bedrock-agentcore:CreatePaymentConnector",
          "bedrock-agentcore:GetPaymentConnector",
          "bedrock-agentcore:ListPaymentConnectors",
          "bedrock-agentcore:DeletePaymentConnector",
          "bedrock-agentcore:UpdatePaymentConnector"
        ],
        Resource: [
          # CreatePaymentConnector authorizes against the parent payment-manager
          # resource; per-connector get/update/delete authorize against the
          # connector sub-resource. Grant both. (The published IAM reference
          # lists only the sub-resource.)
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":payment-manager/*"),
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":payment-manager/*/connector/*")
        ]
      },
      {
        Sid: "AllowCredentialProviderOperations",
        Effect: "Allow",
        Action: [
          "bedrock-agentcore:CreatePaymentCredentialProvider",
          "bedrock-agentcore:GetPaymentCredentialProvider",
          "bedrock-agentcore:ListPaymentCredentialProviders",
          "bedrock-agentcore:DeletePaymentCredentialProvider",
          "bedrock-agentcore:UpdatePaymentCredentialProvider",
          # Creating the first credential provider in an account implicitly
          # provisions the default token vault, so the live preview API also
          # requires CreateTokenVault on token-vault/default. (Not listed in
          # the published IAM reference.)
          "bedrock-agentcore:CreateTokenVault"
        ],
        Resource: [
          # CreatePaymentCredentialProvider / CreateTokenVault / list authorize
          # against the token vault itself; per-provider get/update/delete
          # authorize against the credential-provider sub-resource. Grant both
          # so the full lifecycle works. (The published IAM reference lists only
          # the sub-resource, but the live preview API evaluates the create
          # against "token-vault/default".)
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":token-vault/default"),
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":token-vault/*/paymentcredentialprovider/*")
        ]
      },
      {
        # AgentCore payments stores (and rotates/deletes) the wallet-provider
        # credentials in AWS Secrets Manager on behalf of this role when a
        # credential provider is created / updated / deleted. The secret name
        # is service-chosen at create time, so the resource cannot be pinned to
        # a specific secret ARN here; instead it is scoped to this account and
        # constrained by an aws:ResourceAccount condition. (Not listed in the
        # published IAM reference, which only covers the runtime read path.)
        # Production hardening: once the service secret-name prefix is known,
        # replace the "secret:*" wildcard with that prefix, or add a
        # secretsmanager:ResourceTag condition matching the service tag.
        Sid: "AllowCredentialProviderSecretStorage",
        Effect: "Allow",
        Action: [
          "secretsmanager:CreateSecret",
          "secretsmanager:TagResource",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecret",
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue",
          "secretsmanager:DeleteSecret"
        ],
        Resource: [("arn:aws:secretsmanager:*:" + $accountId + ":secret:*")],
        Condition: {StringEquals: {"aws:ResourceAccount": $accountId}}
      }
    ]
  }'
}

pass_role_policy() {
  jq -n --arg accountId "$ACCOUNT_ID" --arg rrRole "$RESOURCE_RETRIEVAL_ROLE_NAME" '{
    Version: "2012-10-17",
    Statement: [{
      Sid: "AllowPassResourceRetrievalRole",
      Effect: "Allow",
      Action: "iam:PassRole",
      Resource: ("arn:aws:iam::" + $accountId + ":role/" + $rrRole)
    }]
  }'
}

# Instrument + session management scoped to this account's payment-manager
# resources. Production hardening: replace the "*" manager segment with the
# concrete Manager ID once stable, or add a tag-based condition.
management_allow_policy() {
  jq -n --arg accountId "$ACCOUNT_ID" '{
    Version: "2012-10-17",
    Statement: [{
      Sid: "AllowPaymentManagement",
      Effect: "Allow",
      Action: [
        "bedrock-agentcore:CreatePaymentInstrument",
        "bedrock-agentcore:GetPaymentInstrument",
        "bedrock-agentcore:GetPaymentInstrumentBalance",
        "bedrock-agentcore:ListPaymentInstruments",
        "bedrock-agentcore:DeletePaymentInstrument",
        "bedrock-agentcore:CreatePaymentSession",
        "bedrock-agentcore:GetPaymentSession",
        "bedrock-agentcore:ListPaymentSessions",
        "bedrock-agentcore:UpdatePaymentSession",
        "bedrock-agentcore:DeletePaymentSession"
      ],
      Resource: [
        ("arn:aws:bedrock-agentcore:*:" + $accountId + ":payment-manager/*"),
        ("arn:aws:bedrock-agentcore:*:" + $accountId + ":payment-manager/*/instrument/*"),
        ("arn:aws:bedrock-agentcore:*:" + $accountId + ":payment-manager/*/session/*")
      ]
    }]
  }'
}

management_deny_policy() {
  jq -n '{
    Version: "2012-10-17",
    Statement: [{
      Sid: "DenyProcessPayment",
      Effect: "Deny",
      Action: "bedrock-agentcore:ProcessPayment",
      Resource: "*"
    }]
  }'
}

# ProcessPayment is scoped to session resources; the read-only ops are scoped
# to this account's payment-manager resources. Production hardening: replace
# the "*" manager segment with the concrete Manager ID once stable.
process_payment_allow_policy() {
  jq -n --arg accountId "$ACCOUNT_ID" '{
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "AllowProcessPayment",
        Effect: "Allow",
        Action: "bedrock-agentcore:ProcessPayment",
        Resource: [("arn:aws:bedrock-agentcore:*:" + $accountId + ":payment-manager/*/session/*")]
      },
      {
        Sid: "AllowPaymentReadOperations",
        Effect: "Allow",
        Action: [
          "bedrock-agentcore:GetPaymentInstrument",
          "bedrock-agentcore:GetPaymentInstrumentBalance",
          "bedrock-agentcore:GetPaymentSession"
        ],
        Resource: [
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":payment-manager/*"),
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":payment-manager/*/instrument/*"),
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":payment-manager/*/session/*")
        ]
      }
    ]
  }'
}

process_payment_deny_policy() {
  jq -n '{
    Version: "2012-10-17",
    Statement: [{
      Sid: "DenyPaymentManagement",
      Effect: "Deny",
      Action: [
        "bedrock-agentcore:CreatePaymentInstrument",
        "bedrock-agentcore:DeletePaymentInstrument",
        "bedrock-agentcore:CreatePaymentSession",
        "bedrock-agentcore:UpdatePaymentSession"
      ],
      Resource: "*"
    }]
  }'
}

runtime_execution_policy() {
  jq -n --arg accountId "$ACCOUNT_ID" --arg region "$AWS_REGION" '{
    Version: "2012-10-17",
    Statement: [
      {
        # ecr:GetAuthorizationToken must be granted on "*" — the API does not
        # support resource-level permissions for it.
        Sid: "RuntimeECRAuth",
        Effect: "Allow",
        Action: ["ecr:GetAuthorizationToken"],
        Resource: "*"
      },
      {
        # Image-pull actions scoped to the exact repository the CDK stack
        # creates (agent_stack.py -> repository_name
        # "pay-for-x402-secure-data-agent"), following least privilege.
        Sid: "RuntimeECRImagePull",
        Effect: "Allow",
        Action: [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer"
        ],
        Resource: [("arn:aws:ecr:" + $region + ":" + $accountId + ":repository/pay-for-x402-secure-data-agent")]
      },
      {
        Sid: "RuntimeCloudWatchLogs",
        Effect: "Allow",
        Action: [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:PutLogEvents"
        ],
        Resource: [
          ("arn:aws:logs:" + $region + ":" + $accountId + ":log-group:/aws/bedrock-agentcore/runtimes/*"),
          ("arn:aws:logs:" + $region + ":" + $accountId + ":log-group:*")
        ]
      },
      {
        Sid: "RuntimeXRay",
        Effect: "Allow",
        Action: [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets"
        ],
        Resource: "*"
      },
      {
        Sid: "RuntimeCloudWatchMetrics",
        Effect: "Allow",
        Action: "cloudwatch:PutMetricData",
        Resource: "*",
        Condition: {
          StringEquals: {"cloudwatch:namespace": "bedrock-agentcore"}
        }
      },
      {
        Sid: "BedrockModelInvocation",
        Effect: "Allow",
        Action: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ],
        Resource: [
          "arn:aws:bedrock:*::foundation-model/*",
          ("arn:aws:bedrock:*:" + $accountId + ":inference-profile/*"),
          ("arn:aws:bedrock:*:" + $accountId + ":application-inference-profile/*")
        ]
      }
    ]
  }'
}

# The ResourceRetrievalRole is validated at CreatePaymentManager time: the
# service requires the role to already grant the workload-identity + payment
# token "base permissions". This mirrors the documented base-permission set
# (see the "IAM roles for AgentCore payments" reference), scoped to this
# account's default token vault and workload-identity directory — not "*".
resource_retrieval_policy() {
  local base
  base=$(jq -n --arg accountId "$ACCOUNT_ID" '{
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "WorkloadIdentityManagement",
        Effect: "Allow",
        Action: [
          "bedrock-agentcore:CreateWorkloadIdentity",
          "bedrock-agentcore:DeleteWorkloadIdentity"
        ],
        Resource: [
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":workload-identity-directory/default"),
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":workload-identity-directory/default/workload-identity/*")
        ]
      },
      {
        Sid: "WorkloadIdentityAccess",
        Effect: "Allow",
        Action: ["bedrock-agentcore:GetWorkloadAccessToken"],
        Resource: [
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":workload-identity-directory/default"),
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":workload-identity-directory/default/workload-identity/*")
        ]
      },
      {
        Sid: "PaymentTokenBaseAccess",
        Effect: "Allow",
        Action: ["bedrock-agentcore:GetResourcePaymentToken"],
        Resource: [
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":token-vault/default"),
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":workload-identity-directory/default"),
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":workload-identity-directory/default/workload-identity/*"),
          # Per-connector: the service reads the payment token against the
          # credential-provider resource at instrument-create and payment time.
          # The published reference appends this per connector automatically,
          # but the live preview API validates it up front, so grant it here.
          ("arn:aws:bedrock-agentcore:*:" + $accountId + ":token-vault/*/paymentcredentialprovider/*")
        ]
      },
      {
        # Per-connector: at runtime the service reads the wallet-provider secret
        # from Secrets Manager to sign payments. Scoped to secrets in this
        # account (the specific secret ARN is service-chosen). Matches the
        # per-connector SecretsManagerAccess block in the IAM reference.
        Sid: "SecretsManagerAccess",
        Effect: "Allow",
        Action: ["secretsmanager:GetSecretValue"],
        Resource: [("arn:aws:secretsmanager:*:" + $accountId + ":secret:*")],
        Condition: {StringEquals: {"aws:ResourceAccount": $accountId}}
      }
    ]
  }')
  # Optional: if a specific credential-provider secret ARN is known, also allow
  # the role to read it (the service otherwise appends per-connector secret
  # access automatically when a connector is added).
  if [[ -n "${CREDENTIAL_PROVIDER_SECRET_ARN:-}" ]]; then
    echo "$base" | jq --arg secretArn "$CREDENTIAL_PROVIDER_SECRET_ARN" \
      '.Statement += [{Sid:"AllowSpecificCredentialSecret",Effect:"Allow",Action:"secretsmanager:GetSecretValue",Resource:$secretArn}]'
  else
    echo "$base"
  fi
}
