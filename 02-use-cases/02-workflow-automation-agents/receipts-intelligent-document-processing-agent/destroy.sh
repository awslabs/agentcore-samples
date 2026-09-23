#!/bin/bash
set -euo pipefail

# ============================================================================
# Receipts IDP Agent — Teardown
# Usage: ./destroy.sh [region]
#
# The stack is CDK-managed (created by `agentcore deploy`), and this CLI version
# has no `agentcore destroy`. Tear down via CloudFormation delete-stack — the most
# reliable path (verified: `cdk destroy` can silently no-op depending on the CDK
# CLI version, whereas delete-stack always acts on the named stack). With
# destroyOnDelete (the sample default) the DynamoDB tables and S3 bucket go too,
# leaving nothing billable behind.
#
# DELETE_FAILED recovery: the AgentCore control-plane resources (Runtime, Gateway,
# GatewayTarget, PolicyEngine, Evaluator) can occasionally fail to delete on
# control-plane ordering. This script recovers automatically — it retries the
# plain delete once (transient ordering orphans usually clear), then re-issues the
# delete with --retain-resources for anything still stuck so the stack itself
# clears, and finally prints those orphaned resources (type + physical id) so you
# can remove the handful by hand. Nothing is left silently orphaned.
# ============================================================================

REGION="${1:-us-west-2}"
STACK="${RECEIPTS_STACK:-AgentCore-ReceiptsAgent-dev}"
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"

echo "🧹 Destroying $STACK in $REGION..."

if ! aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" >/dev/null 2>&1; then
  echo "✅ Stack $STACK not present — nothing to destroy."
  exit 0
fi

# List the logical IDs currently in DELETE_FAILED (the resources blocking teardown).
failed_resource_ids() {
  aws cloudformation describe-stack-resources --stack-name "$STACK" --region "$REGION" \
    --query "StackResources[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" \
    --output text 2>/dev/null || true
}

# Poll delete-stack to a terminal state. Returns the final StackStatus on stdout
# (DELETE_COMPLETE once the stack is gone, or DELETE_FAILED if it got stuck).
wait_for_delete() {
  while true; do
    local status
    status=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
      --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "DELETE_COMPLETE")
    case "$status" in
      DELETE_COMPLETE) echo "DELETE_COMPLETE"; return 0 ;;
      DELETE_FAILED)   echo "DELETE_FAILED";   return 0 ;;
      *) printf "." >&2; sleep 15 ;;
    esac
  done
}

echo "⏳ Deleting $STACK (this usually takes a few minutes)..."
aws cloudformation delete-stack --stack-name "$STACK" --region "$REGION"
STATUS=$(wait_for_delete)

# Recovery pass 1: a plain retry clears most transient control-plane ordering orphans.
if [ "$STATUS" = "DELETE_FAILED" ]; then
  echo ""
  echo "⚠️  First delete hit DELETE_FAILED (AgentCore control-plane ordering)."
  echo "   Stuck: $(failed_resource_ids | tr '\t' ' ')"
  echo "   Retrying the delete once (ordering orphans usually clear on a second pass)..."
  aws cloudformation delete-stack --stack-name "$STACK" --region "$REGION"
  STATUS=$(wait_for_delete)
fi

# Recovery pass 2: if specific resources are STILL stuck, retain just those so the
# rest of the stack (and everything billable) is removed, then report them for a
# quick manual cleanup rather than leaving the whole stack wedged.
if [ "$STATUS" = "DELETE_FAILED" ]; then
  STUCK=$(failed_resource_ids)
  # Snapshot type + physical id NOW, while the stack still exists — after a
  # successful retain-delete the stack is gone and this info is unrecoverable.
  ORPHANS=$(aws cloudformation describe-stack-resources --stack-name "$STACK" --region "$REGION" \
    --query "StackResources[?ResourceStatus=='DELETE_FAILED'].[ResourceType,PhysicalResourceId]" \
    --output text 2>/dev/null || true)
  echo ""
  echo "⚠️  Still stuck after retry: $(echo "$STUCK" | tr '\t' ' ')"
  echo "   Retaining those resources so the rest of the stack deletes cleanly..."
  # shellcheck disable=SC2086 # intentional word-splitting: pass IDs as separate args
  aws cloudformation delete-stack --stack-name "$STACK" --region "$REGION" \
    --retain-resources $STUCK
  STATUS=$(wait_for_delete)
fi

echo ""
if [ "$STATUS" = "DELETE_COMPLETE" ]; then
  if [ -n "${ORPHANS:-}" ]; then
    echo "✅ Stack $STACK removed. A few control-plane resources were retained and"
    echo "   need a one-time manual delete (physical ids for the AWS console/CLI):"
    echo "$ORPHANS" | while IFS=$'\t' read -r rtype pid; do
      [ -n "$rtype" ] && echo "     • $rtype  →  $pid"
    done
    echo "   See docs/deployment.md → 'Teardown & DELETE_FAILED recovery' for the delete calls."
  else
    echo "✅ Teardown complete — $STACK removed."
  fi
else
  echo "❌ Teardown could not complete automatically ($STATUS)."
  echo "   Inspect the stack events, then delete the remaining resources manually:"
  echo "     aws cloudformation describe-stack-events --stack-name $STACK --region $REGION \\"
  echo "       --query \"StackEvents[?ResourceStatus=='DELETE_FAILED'].[LogicalResourceId,ResourceStatusReason]\" --output table"
  echo "   Guidance: docs/deployment.md → 'Teardown & DELETE_FAILED recovery'."
  exit 1
fi
