# Temporary vendored SDK build

`bedrock-agentcore/runtime/a2a` ([SDK PR #229](https://github.com/aws/bedrock-agentcore-sdk-typescript/pull/229))
is merged but not yet published to npm. Until it is, the agents depend on a
local tarball built from that commit:

```bash
git clone https://github.com/aws/bedrock-agentcore-sdk-typescript.git /tmp/ac-sdk
git -C /tmp/ac-sdk checkout abafc2af
cd /tmp/ac-sdk && npm ci && npm run build && npm pack --pack-destination /tmp
cp /tmp/bedrock-agentcore-0.4.4.tgz <this-sample>/vendor/bedrock-agentcore-a2a.tgz
```

**This directory must be gone before the PR merges.** Replace the
`file:../../vendor/bedrock-agentcore-a2a.tgz` entries in the three
`agents/*/package.json` files with the published version, regenerate
`package-lock.json`, and delete both this directory and the `vendor/*.tgz`
line in `.gitignore`.
