# Temporary vendored SDK build — REMOVE BEFORE MERGE

`bedrock-agentcore/runtime/a2a` ([SDK PR #229](https://github.com/aws/bedrock-agentcore-sdk-typescript/pull/229),
commit `abafc2af`) is merged but not yet published to npm: the latest release,
0.4.4, has no `./runtime/a2a` entry in its exports map. Until a release ships
it, the four workspace packages that import the SDK resolve it from a locally
built tarball instead of the registry.

**This directory must not exist when the PR merges.** The checklist below
removes every trace of it.

## Rebuilding the tarball (needed on any fresh clone)

The tarball is gitignored, so `npm ci` fails without it:

```bash
git clone https://github.com/aws/bedrock-agentcore-sdk-typescript.git /tmp/ac-sdk
git -C /tmp/ac-sdk checkout abafc2af
cd /tmp/ac-sdk && npm ci && npm run build && npm pack --pack-destination /tmp
cp /tmp/bedrock-agentcore-0.4.4.tgz <this-sample>/vendor/bedrock-agentcore-a2a.tgz
```

## Removal checklist (once the SDK publishes A2A support)

Run from this sample's root directory.

- [ ] **1. Confirm the release carries the subpath.** A version alone is not
      enough — check the exports map:

      npm view bedrock-agentcore version
      npm view bedrock-agentcore exports --json | grep runtime/a2a

      Expect a version above 0.4.4 and a `./runtime/a2a` entry.

- [ ] **2. Repin the four packages.** Replace
      `"bedrock-agentcore": "file:../../vendor/bedrock-agentcore-a2a.tgz"`
      with the published version (e.g. `"^0.5.0"`) in:

      agents/lead/package.json
      agents/log-analyst/package.json
      agents/runbook/package.json
      packages/claude-a2a-executor/package.json

- [ ] **3. Check the peer dependencies still match.** The SDK declares
      `@a2a-js/sdk` and `express` as optional peers. If the release widened or
      moved those ranges, align the pins in the two worker packages
      (`@a2a-js/sdk` 1.0.0, `express` 5.2.1) and in
      `packages/claude-a2a-executor` (`express` as a devDependency).

- [ ] **4. Drop the image copy.** Delete the `COPY vendor ./vendor` line and
      its two-line comment from `docker/agent.Dockerfile`.

- [ ] **5. Drop the gitignore entry.** Delete `vendor/*.tgz` and its comment
      from `.gitignore`.

- [ ] **6. Delete this directory.** `rm -rf vendor`

- [ ] **7. Regenerate the lockfile and verify.** `package-lock.json` carries
      five `file:` references that must all become registry URLs:

      rm -rf node_modules && npm install
      grep -c "vendor/bedrock-agentcore-a2a" package-lock.json   # expect 0
      npm run build && npm run lint && npm test

- [ ] **8. Fix the docs.** Remove prerequisite 5 ("A local `bedrock-agentcore`
      build") from `README.md` and renumber the CDK-bootstrap item back to 5.

- [ ] **9. Rebuild the image.** The agent image installs dependencies
      internally, so it must be rebuilt to pick up the registry version:

      docker compose build --no-cache
      docker compose up -d && docker compose ps    # both workers healthy

- [ ] **10. Re-verify deployed.** `./deploy.sh <region>` then
      `AWS_REGION=<region> ./invoke.sh '<lead-arn>'` — the A2A path is the
      thing this dependency provides, so it is worth one deployed run.

- [ ] **11. Update the PR description.** Remove the "Do not merge yet" note
      about this directory.
