#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Layer 05 - Public static site (S3 + CloudFront + OAC).
#   up   : build the SPA, sync to a PRIVATE S3 bucket, create/reuse an OAC + a
#          CloudFront distribution (SPA error routing, redirect-to-https), attach
#          the OAC bucket policy, persist SITE_* ids, and invalidate on re-run.
#   down : disable the distribution (wait Deployed) -> delete it -> delete the OAC
#          -> empty + delete the bucket. Reads SITE_* ids from state. Idempotent.
#
# PERMISSIONS: `up` needs s3:CreateBucket / PutBucketPolicy / PutPublicAccessBlock
# / PutObject plus cloudfront:CreateOriginAccessControl / CreateDistribution.
# `down` additionally needs s3:DeleteObject / DeleteBucket and the CloudFront
# update/delete actions. If the bucket-policy call is refused, `up` keeps going and
# prints the exact command so you can apply it separately.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"
ACTION="${1:-up}"

valid_cloudfront_id() {
  [ -n "${1:-}" ] && [ "$1" != "None" ] && [[ "$1" =~ ^[A-Z0-9]+$ ]]
}

valid_cloudfront_domain() {
  [ -n "${1:-}" ] && [ "$1" != "None" ] && [[ "$1" =~ ^[A-Za-z0-9-]+\.cloudfront\.net$ ]]
}

remove_site_state_keys() {
  [ -f "$STATE_FILE" ] || return 0
  local pattern="$1" tmp
  tmp="$(mktemp "$STATE_DIR/outputs.env.XXXXXX")"
  if grep -vE "$pattern" "$STATE_FILE" > "$tmp"; then :; else
    [ "$?" -eq 1 ] || { rm -f "$tmp"; die "could not update $STATE_FILE"; }
  fi
  mv "$tmp" "$STATE_FILE"
}

record_site_state() {
  local tmp
  mkdir -p "$STATE_DIR"
  tmp="$(mktemp "$STATE_DIR/outputs.env.XXXXXX")"
  if [ -f "$STATE_FILE" ]; then
    grep -vE '^SITE_(OAC_ID|DIST_ID|DOMAIN|BUCKET)=' "$STATE_FILE" > "$tmp" || [ "$?" -eq 1 ] || {
      rm -f "$tmp"; die "could not update $STATE_FILE";
    }
  fi
  {
    printf 'SITE_BUCKET=%s\n' "$BUCKET"
    printf 'SITE_OAC_ID=%s\n' "$SITE_OAC_ID"
    printf 'SITE_DIST_ID=%s\n' "$SITE_DIST_ID"
    printf 'SITE_DOMAIN=%s\n' "$SITE_DOMAIN"
  } >> "$tmp"
  mv "$tmp" "$STATE_FILE"
}

valid_oac_configuration() {
  local output="$1" expected_id="$2" returned_id signing_protocol signing_behavior origin_type extra
  [[ "$output" != *$'\n'* ]] || return 1
  IFS=$'\t' read -r returned_id signing_protocol signing_behavior origin_type extra <<< "$output"
  [ "$output" = "$returned_id"$'\t'"$signing_protocol"$'\t'"$signing_behavior"$'\t'"$origin_type" ] &&
    valid_cloudfront_id "$returned_id" && [ "$returned_id" = "$expected_id" ] &&
    [ "$signing_protocol" = "sigv4" ] && [ "$signing_behavior" = "always" ] &&
    [ "$origin_type" = "s3" ] && [ -z "$extra" ]
}

validate_discovered_oac() {
  local oac_id="$1" err_file configuration
  err_file="$(mktemp)"
  if configuration="$(aws cloudfront get-origin-access-control --id "$oac_id" \
      --query '[OriginAccessControl.Id,OriginAccessControl.OriginAccessControlConfig.SigningProtocol,OriginAccessControl.OriginAccessControlConfig.SigningBehavior,OriginAccessControl.OriginAccessControlConfig.OriginAccessControlOriginType]' \
      --output text 2>"$err_file")"; then
    rm -f "$err_file"
    valid_oac_configuration "$configuration" "$oac_id" ||
      die "could not validate discovered OAC $oac_id; AWS returned a malformed or insecure signing configuration, refusing to reuse it"
  else
    cat "$err_file" >&2
    rm -f "$err_file"
    die "could not validate discovered OAC $oac_id; refusing to reuse it"
  fi
}

validate_site_oac_state() {
  [ -n "${SITE_OAC_ID:-}" ] && [ "$SITE_OAC_ID" != "None" ] || return 0
  valid_cloudfront_id "$SITE_OAC_ID" || die "recorded OAC ID is malformed; refusing to modify AWS resources"

  local err_file configuration
  err_file="$(mktemp)"
  if configuration="$(aws cloudfront get-origin-access-control --id "$SITE_OAC_ID" \
      --query '[OriginAccessControl.Id,OriginAccessControl.OriginAccessControlConfig.SigningProtocol,OriginAccessControl.OriginAccessControlConfig.SigningBehavior,OriginAccessControl.OriginAccessControlConfig.OriginAccessControlOriginType]' \
      --output text 2>"$err_file")"; then
    rm -f "$err_file"
    valid_oac_configuration "$configuration" "$SITE_OAC_ID" ||
      die "could not validate OAC $SITE_OAC_ID; AWS returned a malformed or insecure signing configuration, refusing to modify AWS resources"
  elif grep -Fq 'An error occurred (NoSuchOriginAccessControl) when calling the GetOriginAccessControl operation' "$err_file"; then
    cat "$err_file" >&2
    rm -f "$err_file"
    warn "recorded OAC $SITE_OAC_ID no longer exists; clearing stale local state"
    remove_site_state_keys '^SITE_OAC_ID='
    SITE_OAC_ID=""
  else
    cat "$err_file" >&2
    rm -f "$err_file"
    die "could not validate OAC $SITE_OAC_ID; refusing to modify AWS resources"
  fi
}

validate_site_distribution_state() {
  [ -n "${SITE_DIST_ID:-}" ] && [ "$SITE_DIST_ID" != "None" ] || return 0
  valid_cloudfront_id "$SITE_DIST_ID" || die "recorded distribution ID is malformed; refusing to modify AWS resources"

  local err_file domain
  err_file="$(mktemp)"
  if domain="$(aws cloudfront get-distribution --id "$SITE_DIST_ID" \
      --query 'Distribution.DomainName' --output text 2>"$err_file")"; then
    rm -f "$err_file"
    valid_cloudfront_domain "$domain" ||
      die "could not inspect distribution $SITE_DIST_ID; AWS returned an empty or malformed domain, refusing to create a replacement"
    SITE_DOMAIN="$domain"
  elif grep -Fq 'An error occurred (NoSuchDistribution) when calling the GetDistribution operation' "$err_file"; then
    cat "$err_file" >&2
    rm -f "$err_file"
    warn "recorded distribution $SITE_DIST_ID no longer exists; clearing stale local state"
    remove_site_state_keys '^SITE_(DIST_ID|DOMAIN)='
    SITE_DIST_ID=""
    SITE_DOMAIN=""
  else
    cat "$err_file" >&2
    rm -f "$err_file"
    die "could not inspect distribution $SITE_DIST_ID; refusing to create a replacement"
  fi
}

site_up() {
  step "Layer 05 UP - Deploy the static site (S3 + CloudFront + OAC)"
  [ -f "$FRONTEND_ENV" ] || die "missing $FRONTEND_ENV - run ./02-config.sh up first"
  if ! run_capture ACCOUNT aws sts get-caller-identity --query Account --output text; then
    die "could not determine the AWS account; refusing to deploy"
  fi
  [[ "$ACCOUNT" =~ ^[0-9]{12}$ ]] || die "AWS returned an invalid account ID; refusing to deploy"
  if ! run_capture CALLER_ARN aws sts get-caller-identity --query Arn --output text; then
    die "could not determine the AWS partition; refusing to deploy"
  fi
  PART="$(printf '%s' "$CALLER_ARN" | cut -d: -f2)"
  [[ "$PART" =~ ^aws(-cn|-us-gov)?$ ]] || die "AWS returned an invalid caller ARN; refusing to deploy"
  BUCKET="${SITE_BUCKET:-${APP_NAME}-site-$ACCOUNT}"; OAC_NAME="${APP_NAME}-oac"
  info "Account=$ACCOUNT Region=$AWS_REGION Bucket=$BUCKET"
  SITE_OAC_ID=""; SITE_DIST_ID=""; SITE_DOMAIN=""
  load_state_ids; SITE_OAC_ID="${SITE_OAC_ID:-}"; SITE_DIST_ID="${SITE_DIST_ID:-}"; SITE_DOMAIN="${SITE_DOMAIN:-}"
  validate_site_oac_state
  validate_site_distribution_state

  step "Building the production bundle"
  # A fresh clone has no node_modules (tsc/vite come from devDependencies).
  if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    info "Installing frontend dependencies (npm ci)"
    ( cd "$FRONTEND_DIR" && run npm ci )
  fi
  ( cd "$FRONTEND_DIR" && run npm run build )
  [ -d "$FRONTEND_DIR/dist" ] || die "no dist/ produced by the build"

  step "Ensuring the S3 bucket exists (private)"
  if run aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then ok "bucket exists"; else
    info "creating bucket $BUCKET"
    if [ "$AWS_REGION" = "us-east-1" ]; then run aws s3api create-bucket --bucket "$BUCKET" --region us-east-1
    else run aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION" --create-bucket-configuration "LocationConstraint=$AWS_REGION"; fi
  fi
  # Apply all four controls on every run, including when reusing an existing bucket.
  run aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  step "Syncing frontend/dist -> s3://$BUCKET"
  run aws s3 sync "$FRONTEND_DIR/dist" "s3://$BUCKET" --delete && ok "objects uploaded"

  step "Ensuring a CloudFront Origin Access Control exists"
  if [ -z "$SITE_OAC_ID" ]; then
    if ! run_capture DISCOVERED_OAC_ID aws cloudfront list-origin-access-controls \
        --query "OriginAccessControlList.Items[?Name=='$OAC_NAME'].Id | [0]" --output text; then
      die "could not list origin access controls; refusing to create one"
    fi
    if [ "$DISCOVERED_OAC_ID" = "None" ]; then
      SITE_OAC_ID=""
    elif valid_cloudfront_id "$DISCOVERED_OAC_ID"; then
      validate_discovered_oac "$DISCOVERED_OAC_ID"
      SITE_OAC_ID="$DISCOVERED_OAC_ID"
    else
      die "could not discover the OAC; AWS returned an empty or malformed ID, refusing to create one"
    fi
  fi
  if [ -z "$SITE_OAC_ID" ]; then
    if ! run_capture SITE_OAC_ID aws cloudfront create-origin-access-control \
        --origin-access-control-config "Name=$OAC_NAME,SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3" \
        --query 'OriginAccessControl.Id' --output text; then
      die "could not create the origin access control; refusing to create a distribution"
    fi
    valid_cloudfront_id "$SITE_OAC_ID" ||
      die "AWS returned an empty or malformed OAC ID; refusing to create a distribution"
    ok "created OAC $SITE_OAC_ID"
  else
    valid_cloudfront_id "$SITE_OAC_ID" || die "OAC ID is malformed; refusing to create a distribution"
    ok "reusing OAC $SITE_OAC_ID"
  fi

  step "Ensuring a CloudFront distribution exists"
  if [ -z "$SITE_DIST_ID" ] || [ "$SITE_DIST_ID" = "None" ]; then
    DIST_CFG="$STATE_DIR/cloudfront-config.json"; mkdir -p "$STATE_DIR"
    cat > "$DIST_CFG" <<JSON
{ "CallerReference": "${APP_NAME}-$(date +%s)", "Comment": "${APP_NAME} static site",
  "Enabled": true, "DefaultRootObject": "index.html",
  "Origins": { "Quantity": 1, "Items": [ { "Id": "s3-$BUCKET",
    "DomainName": "$BUCKET.s3.$AWS_REGION.amazonaws.com",
    "OriginAccessControlId": "$SITE_OAC_ID", "S3OriginConfig": { "OriginAccessIdentity": "" } } ] },
  "DefaultCacheBehavior": { "TargetOriginId": "s3-$BUCKET", "ViewerProtocolPolicy": "redirect-to-https",
    "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6", "Compress": true,
    "AllowedMethods": { "Quantity": 2, "Items": ["GET","HEAD"], "CachedMethods": { "Quantity": 2, "Items": ["GET","HEAD"] } } },
  "CustomErrorResponses": { "Quantity": 2, "Items": [
    { "ErrorCode": 403, "ResponsePagePath": "/index.html", "ResponseCode": "200", "ErrorCachingMinTTL": 10 },
    { "ErrorCode": 404, "ResponsePagePath": "/index.html", "ResponseCode": "200", "ErrorCachingMinTTL": 10 } ] },
  "PriceClass": "PriceClass_100" }
JSON
    if ! run_capture DJSON aws cloudfront create-distribution --distribution-config "file://$DIST_CFG" \
        --query '{Id:Distribution.Id,Domain:Distribution.DomainName}' --output json; then
      die "could not create the CloudFront distribution; refusing to apply policy or record site state"
    fi
    DJSON_COMPACT="$(printf '%s' "$DJSON" | tr -d '[:space:]')"
    SITE_DIST_ID="$(printf '%s' "$DJSON_COMPACT" | sed -n 's/^{"Id":"\([^"]*\)","Domain":"[^"]*"}$/\1/p')"
    SITE_DOMAIN="$(printf '%s' "$DJSON_COMPACT" | sed -n 's/^{"Id":"[^"]*","Domain":"\([^"]*\)"}$/\1/p')"
    [ "$DJSON_COMPACT" = "{\"Id\":\"$SITE_DIST_ID\",\"Domain\":\"$SITE_DOMAIN\"}" ] &&
      valid_cloudfront_id "$SITE_DIST_ID" && valid_cloudfront_domain "$SITE_DOMAIN" ||
      die "AWS returned empty or malformed distribution identifiers; refusing to apply policy or record site state"
    ok "created distribution $SITE_DIST_ID ($SITE_DOMAIN)"
  else
    valid_cloudfront_id "$SITE_DIST_ID" && valid_cloudfront_domain "$SITE_DOMAIN" ||
      die "saved distribution identifiers are malformed; refusing dependent operations"
    ok "reusing distribution $SITE_DIST_ID ($SITE_DOMAIN)"
    info "invalidating the edge cache"
    run aws cloudfront create-invalidation --distribution-id "$SITE_DIST_ID" --paths '/*' \
      --query 'Invalidation.Status' --output text || warn "invalidation failed (non-fatal)"
  fi

  step "Granting the distribution read access (OAC bucket policy)"
  POLICY_FILE="$STATE_DIR/bucket-policy.json"
  cat > "$POLICY_FILE" <<JSON
{ "Version": "2012-10-17", "Statement": [ { "Sid": "AllowCloudFrontServicePrincipalReadOnly",
  "Effect": "Allow", "Principal": { "Service": "cloudfront.amazonaws.com" }, "Action": "s3:GetObject",
  "Resource": "arn:$PART:s3:::$BUCKET/*",
  "Condition": { "StringEquals": { "AWS:SourceArn": "arn:$PART:cloudfront::$ACCOUNT:distribution/$SITE_DIST_ID" } } } ] }
JSON
  if aws s3api put-bucket-policy --bucket "$BUCKET" --policy "file://$POLICY_FILE" 2>/dev/null; then
    ok "bucket policy applied"
  else
    warn "could not apply the bucket policy automatically (managed-session verb block). Run:"
    printf '      aws s3api put-bucket-policy --bucket %s --policy file://%s\n' "$BUCKET" "$POLICY_FILE"
  fi

  step "Recording site ids in deploy/state/outputs.env"
  record_site_state
  ok "recorded SITE_* ids"

  info "Waiting for the distribution to reach Deployed (can take ~10-15 min)..."
  run aws cloudfront wait distribution-deployed --id "$SITE_DIST_ID"
  ok "distribution is Deployed"
  step "Layer 05 UP complete"
  info "Public URL (allow ~10-15 min on first deploy): https://$SITE_DOMAIN"
}

site_down() {
  step "Layer 05 DOWN - Remove the static site (CloudFront + OAC + S3)"
  load_state_ids
  SITE_DIST_ID="${SITE_DIST_ID:-}"; SITE_OAC_ID="${SITE_OAC_ID:-}"
  SITE_BUCKET="${SITE_BUCKET:-${APP_NAME}-site-$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)}"

  if [ -n "$SITE_DIST_ID" ] && aws cloudfront get-distribution --id "$SITE_DIST_ID" >/dev/null 2>&1; then
    ENABLED="$(aws cloudfront get-distribution --id "$SITE_DIST_ID" --query 'Distribution.DistributionConfig.Enabled' --output text 2>/dev/null || true)"
    if [ "$ENABLED" = "True" ]; then
      info "Disabling distribution $SITE_DIST_ID (must be disabled + deployed before delete)"
      TMP="$STATE_DIR/_cf_cfg.json"; mkdir -p "$STATE_DIR"
      ETAG="$(aws cloudfront get-distribution-config --id "$SITE_DIST_ID" --query ETag --output text || true)"
      aws cloudfront get-distribution-config --id "$SITE_DIST_ID" --query DistributionConfig > "$TMP" \
        || warn "could not read the distribution config"
      if command -v python3 >/dev/null 2>&1; then
        python3 -c "import json;p='$TMP';d=json.load(open(p));d['Enabled']=False;json.dump(d,open(p,'w'))" \
          || warn "could not edit the distribution config"
      else sed -i.bak 's/"Enabled": *true/"Enabled": false/' "$TMP"; rm -f "$TMP.bak"; fi
      run aws cloudfront update-distribution --id "$SITE_DIST_ID" --distribution-config "file://$TMP" --if-match "$ETAG" \
        --query 'Distribution.Status' --output text >/dev/null && ok "disable requested"
      rm -f "$TMP"
    fi
    info "Waiting for the distribution to reach Deployed (can take ~10-15 min)..."
    run aws cloudfront wait distribution-deployed --id "$SITE_DIST_ID" 2>/dev/null || warn "wait timed out; re-run to finish"
    DETAG="$(aws cloudfront get-distribution-config --id "$SITE_DIST_ID" --query ETag --output text 2>/dev/null || true)"
    run aws cloudfront delete-distribution --id "$SITE_DIST_ID" --if-match "$DETAG" 2>/dev/null \
      && ok "distribution deleted" || warn "delete deferred - must be Deployed + disabled; re-run once it is"
  else ok "no CloudFront distribution to delete"; fi

  if [ -n "$SITE_OAC_ID" ] && aws cloudfront get-origin-access-control --id "$SITE_OAC_ID" >/dev/null 2>&1; then
    OETAG="$(aws cloudfront get-origin-access-control --id "$SITE_OAC_ID" --query ETag --output text 2>/dev/null || true)"
    run aws cloudfront delete-origin-access-control --id "$SITE_OAC_ID" --if-match "$OETAG" 2>/dev/null \
      && ok "OAC deleted" || warn "OAC delete deferred (detach from the deleted distribution first)"
  else ok "no OAC to delete"; fi

  if [ -n "$SITE_BUCKET" ] && aws s3api head-bucket --bucket "$SITE_BUCKET" 2>/dev/null; then
    info "Emptying + deleting bucket $SITE_BUCKET"
    run aws s3 rm "s3://$SITE_BUCKET" --recursive >/dev/null 2>&1 || warn "empty failed"
    run aws s3api delete-bucket --bucket "$SITE_BUCKET" 2>/dev/null && ok "bucket deleted" || warn "bucket delete failed (not empty?)"
  else ok "no S3 bucket to delete"; fi
  step "Layer 05 DOWN complete"
}

case "$ACTION" in
  up)   site_up ;;
  down) site_down ;;
  *)    die "usage: $0 up|down" ;;
esac
