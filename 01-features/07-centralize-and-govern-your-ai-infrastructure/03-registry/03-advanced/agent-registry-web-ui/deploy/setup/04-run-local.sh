#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Step 4 - Build and run the SPA locally so you can validate all 3 personas
# against the real (just-deployed) Cognito pools + registry. Serves the
# production build on http://127.0.0.1:4173/.
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

step "Step 4/5 - Build + preview the SPA locally"
[ -f "$FRONTEND_ENV" ] || die "missing $FRONTEND_ENV - run ./02-config.sh up first"

info "Config the SPA will use (public identifiers only):"
run sed -e 's/\(=.*\)/\1/' "$FRONTEND_ENV" | sed 's/^/      /'

info "Installing dependencies (npm ci if lockfile is in sync, else npm install)"
if [ -f "$FRONTEND_DIR/package-lock.json" ]; then
  ( cd "$FRONTEND_DIR" && run npm ci ) || ( cd "$FRONTEND_DIR" && run npm install )
else
  ( cd "$FRONTEND_DIR" && run npm install )
fi

info "Type-checking + production build"
( cd "$FRONTEND_DIR" && run npm run build )
ok "build succeeded (frontend/dist/)"

step "Starting the preview server on http://127.0.0.1:4173/"
info "This runs in the foreground; press Ctrl-C to stop."
info "Log in with the persona users seeded in Step 3 (password from deploy/state/persona-password.txt)."
info "Validate IAM enforcement: a Consumer's 'Create record' should fail with AccessDenied."
( cd "$FRONTEND_DIR" && run npm run preview )
