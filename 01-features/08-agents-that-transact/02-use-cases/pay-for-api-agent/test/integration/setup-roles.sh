#!/usr/bin/env bash
# setup-roles.sh — create the four IAM roles the notebook assumes into.
#
# Creates, idempotently:
#   AgentCorePaymentsControlPlaneRole     — manages Manager/Connector/CredentialProvider
#   AgentCorePaymentsManagementRole       — manages Instrument/Session (explicit Deny on ProcessPayment)
#   AgentCorePaymentsProcessPaymentRole   — signs payments, reads Instrument/Session
#   AgentCorePaymentsResourceRetrievalRole — service-assumed, retrieves credentials at runtime
#
# Policies are based on the four-role separation-of-duties model
# recommended for AgentCore Payments (ControlPlane / Management /
# ProcessPayment / ResourceRetrieval — see the main README for the
# full policy text).
# After creating the roles, writes their ARNs into the use-case .env so the
# notebook picks them up without further editing.
#
# Re-running is safe: existing roles are left alone, their policies are
# updated in place, and .env values are only written if empty.
#
# Usage:
#   bash test/integration/setup-roles.sh

set -euo pipefail

# ── Path plumbing ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USE_CASE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${USE_CASE_ROOT}/.env"
TEMPLATE="${USE_CASE_ROOT}/env-sample.txt"

# ── Prerequisites ─────────────────────────────────────────────────────
command -v aws >/dev/null 2>&1 || {
    echo "❌ aws CLI not found — install AWS CLI v2 first." >&2
    exit 1
}

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
if [ -z "${ACCOUNT_ID}" ] || [ "${ACCOUNT_ID}" = "None" ]; then
    echo "❌ Could not resolve AWS account. Run 'aws configure' first." >&2
    exit 1
fi

echo "✅ Account: ${ACCOUNT_ID}"
echo

# ── Role definitions ──────────────────────────────────────────────────
CP_ROLE="AgentCorePaymentsControlPlaneRole"
MGMT_ROLE="AgentCorePaymentsManagementRole"
PP_ROLE="AgentCorePaymentsProcessPaymentRole"
RR_ROLE="AgentCorePaymentsResourceRetrievalRole"

# Standard account trust policy — lets any IAM principal in this account
# assume the role. Good enough for a tutorial; tighten for production.
ACCOUNT_TRUST_POLICY=$(cat <<JSON
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"AWS": "arn:aws:iam::${ACCOUNT_ID}:root"},
            "Action": "sts:AssumeRole"
        }
    ]
}
JSON
)

# Service trust policy for the ResourceRetrievalRole. The service assumes
# it on behalf of whichever Payment Manager it is acting for; the condition
# keys scope access to this account.
SERVICE_TRUST_POLICY=$(cat <<JSON
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": "${ACCOUNT_ID}"}
            }
        }
    ]
}
JSON
)

# ── ControlPlaneRole policy ───────────────────────────────────────────
RR_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${RR_ROLE}"

CP_POLICY=$(cat <<JSON
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AllowPaymentManagerOperations",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:CreatePaymentManager",
                "bedrock-agentcore:GetPaymentManager",
                "bedrock-agentcore:ListPaymentManagers",
                "bedrock-agentcore:DeletePaymentManager",
                "bedrock-agentcore:UpdatePaymentManager"
            ],
            "Resource": ["arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:payment-manager/*"]
        },
        {
            "Sid": "AllowPaymentConnectorOperations",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:CreatePaymentConnector",
                "bedrock-agentcore:GetPaymentConnector",
                "bedrock-agentcore:ListPaymentConnectors",
                "bedrock-agentcore:DeletePaymentConnector",
                "bedrock-agentcore:UpdatePaymentConnector"
            ],
            "Resource": ["arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:payment-manager/*/connector/*"]
        },
        {
            "Sid": "AllowCredentialProviderOperations",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:CreatePaymentCredentialProvider",
                "bedrock-agentcore:GetPaymentCredentialProvider",
                "bedrock-agentcore:ListPaymentCredentialProviders",
                "bedrock-agentcore:DeletePaymentCredentialProvider",
                "bedrock-agentcore:UpdatePaymentCredentialProvider"
            ],
            "Resource": ["arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:token-vault/*/paymentcredentialprovider/*"]
        },
        {
            "Sid": "AllowVendedLogDelivery",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:AllowVendedLogDeliveryForResource"],
            "Resource": ["arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:payment-manager/*"]
        },
        {
            "Sid": "AllowPassResourceRetrievalRole",
            "Effect": "Allow",
            "Action": "iam:PassRole",
            "Resource": "${RR_ROLE_ARN}",
            "Condition": {
                "StringEquals": {"iam:PassedToService": "bedrock-agentcore.amazonaws.com"}
            }
        }
    ]
}
JSON
)

# ── ManagementRole policy ─────────────────────────────────────────────
# This role manages every PaymentManager / Instrument / Session in the
# account. Wildcards on the Resource line scope to the account but not
# to a specific Manager because the Manager IDs do not exist at role
# creation time (the notebook creates them in §4).
#
# Production hardening: once Manager IDs are stable, replace the `*`
# segments with concrete IDs (for example,
# `payment-manager/${MANAGER_ID}`) or add a tag-based condition such as
# `"Condition": {"StringLike": {"aws:ResourceTag/Project": "pay-for-api"}}`
# to confine the role to tagged resources.
MGMT_POLICY=$(cat <<JSON
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AllowPaymentManagement",
            "Effect": "Allow",
            "Action": [
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
            "Resource": [
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:payment-manager/*",
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:payment-manager/*/instrument/*",
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:payment-manager/*/session/*"
            ]
        },
        {
            "Sid": "DenyProcessPayment",
            "Effect": "Deny",
            "Action": "bedrock-agentcore:ProcessPayment",
            "Resource": "*"
        }
    ]
}
JSON
)

# ── ProcessPaymentRole policy ─────────────────────────────────────────
PP_POLICY=$(cat <<JSON
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AllowProcessPayment",
            "Effect": "Allow",
            "Action": "bedrock-agentcore:ProcessPayment",
            "Resource": ["arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:payment-manager/*/session/*"]
        },
        {
            "Sid": "AllowPaymentReadOperations",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:GetPaymentInstrument",
                "bedrock-agentcore:GetPaymentInstrumentBalance",
                "bedrock-agentcore:GetPaymentSession"
            ],
            "Resource": [
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:payment-manager/*",
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:payment-manager/*/instrument/*",
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:payment-manager/*/session/*"
            ]
        }
    ]
}
JSON
)

# ── ResourceRetrievalRole policy ──────────────────────────────────────
# Base permissions only. Per-connector permissions are appended by the
# service itself when a connector is added to the Manager.
RR_POLICY=$(cat <<JSON
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "WorkloadIdentityCreation",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:CreateWorkloadIdentity"],
            "Resource": [
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:workload-identity-directory/default",
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:workload-identity-directory/default/workload-identity/*"
            ]
        },
        {
            "Sid": "WorkloadIdentityAccess",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:GetWorkloadAccessToken"],
            "Resource": [
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:workload-identity-directory/default",
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:workload-identity-directory/default/workload-identity/*"
            ]
        },
        {
            "Sid": "PaymentTokenBaseAccess",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:GetResourcePaymentToken"],
            "Resource": [
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:token-vault/default",
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:workload-identity-directory/default",
                "arn:aws:bedrock-agentcore:*:${ACCOUNT_ID}:workload-identity-directory/default/workload-identity/*"
            ]
        }
    ]
}
JSON
)

# ── Helpers ───────────────────────────────────────────────────────────
role_exists() {
    aws iam get-role --role-name "$1" >/dev/null 2>&1
}

create_or_update_role() {
    local name="$1"
    local trust="$2"
    local policy_name="$3"
    local policy_doc="$4"

    if role_exists "${name}"; then
        echo "  ↺ ${name} already exists — updating trust + policy"
        aws iam update-assume-role-policy \
            --role-name "${name}" \
            --policy-document "${trust}" >/dev/null
    else
        echo "  + Creating ${name}"
        aws iam create-role \
            --role-name "${name}" \
            --assume-role-policy-document "${trust}" \
            --description "AgentCore Payments tutorial role" >/dev/null
    fi

    aws iam put-role-policy \
        --role-name "${name}" \
        --policy-name "${policy_name}" \
        --policy-document "${policy_doc}" >/dev/null
    echo "    ↳ policy ${policy_name} applied"
}

# ── Create / update roles ─────────────────────────────────────────────
echo "=== Creating / updating IAM roles ==="
create_or_update_role "${CP_ROLE}"   "${ACCOUNT_TRUST_POLICY}" "ControlPlanePolicy"     "${CP_POLICY}"
create_or_update_role "${MGMT_ROLE}" "${ACCOUNT_TRUST_POLICY}" "ManagementPolicy"       "${MGMT_POLICY}"
create_or_update_role "${PP_ROLE}"   "${ACCOUNT_TRUST_POLICY}" "ProcessPaymentPolicy"   "${PP_POLICY}"
create_or_update_role "${RR_ROLE}"   "${SERVICE_TRUST_POLICY}" "ResourceRetrievalPolicy" "${RR_POLICY}"
echo

CP_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${CP_ROLE}"
MGMT_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${MGMT_ROLE}"
PP_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${PP_ROLE}"

echo "=== Role ARNs ==="
echo "  CONTROL_PLANE_ROLE_ARN:      ${CP_ROLE_ARN}"
echo "  MANAGEMENT_ROLE_ARN:         ${MGMT_ROLE_ARN}"
echo "  PROCESS_PAYMENT_ROLE_ARN:    ${PP_ROLE_ARN}"
echo "  RESOURCE_RETRIEVAL_ROLE_ARN: ${RR_ROLE_ARN}"
echo

# ── Write ARNs back to .env ───────────────────────────────────────────
# Only set values for keys that are empty or have the <ACCOUNT_ID> placeholder.
# Never clobber a hand-edited value.
if [ ! -f "${ENV_FILE}" ]; then
    if [ -f "${TEMPLATE}" ]; then
        cp "${TEMPLATE}" "${ENV_FILE}"
        echo "  Seeded ${ENV_FILE} from env-sample.txt"
    else
        touch "${ENV_FILE}"
        echo "  Created empty ${ENV_FILE}"
    fi
fi

write_env_var() {
    local key="$1"
    local value="$2"
    # Match KEY=, KEY=<…>, or KEY=arn:aws:iam::<ACCOUNT_ID>:…
    local current
    current="$(awk -F '=' -v k="${key}" '$1 == k { sub(/^[^=]+=/, ""); print; exit }' "${ENV_FILE}" 2>/dev/null || true)"

    case "${current}" in
        "" | "<"* | *"<ACCOUNT_ID>"*)
            if grep -q "^${key}=" "${ENV_FILE}"; then
                # in-place update using a tmp file so we don't depend on sed -i flavour
                awk -F '=' -v k="${key}" -v v="${value}" \
                    '{ if ($1 == k) print k "=" v; else print $0 }' "${ENV_FILE}" > "${ENV_FILE}.tmp"
                mv "${ENV_FILE}.tmp" "${ENV_FILE}"
            else
                echo "${key}=${value}" >> "${ENV_FILE}"
            fi
            echo "  ✅ Wrote ${key} to .env"
            ;;
        *)
            echo "  ↷ ${key} already set — leaving alone (${current})"
            ;;
    esac
}

echo "=== Updating ${ENV_FILE} ==="
write_env_var "CONTROL_PLANE_ROLE_ARN"      "${CP_ROLE_ARN}"
write_env_var "MANAGEMENT_ROLE_ARN"         "${MGMT_ROLE_ARN}"
write_env_var "PROCESS_PAYMENT_ROLE_ARN"    "${PP_ROLE_ARN}"
write_env_var "RESOURCE_RETRIEVAL_ROLE_ARN" "${RR_ROLE_ARN}"

echo
echo "✅ Done. Next: run the §2 setup cell in the notebook to fill in credentials"
