# syntax=docker/dockerfile:1.7
# The dev box: openvscode-server, Claude Code (CLI + VS Code extension), the AWS CLI and the MCP
# proxy, behind the Node proxy that is the box's only way in. Every download is pinned and checked.
#   docker buildx build --platform linux/arm64 --load -t devbox-box:dev .

# The base: Ubuntu 24.04 from ECR Public (the Docker Official Images mirror), pinned by the digest of its
# multi-arch index (both stages pick its linux/arm64 image). To update it, change the digest here.
ARG UBUNTU=public.ecr.aws/docker/library/ubuntu:24.04@sha256:a853f94d226358a79c740cfc7bce0c289748f3fe3488d921d038ccd752c61b60

# ---- fetch: download each pinned artifact and verify it before anything else sees it ----------
FROM --platform=linux/arm64 ${UBUNTU} AS fetch
ARG OVS_VERSION=1.109.5
ARG OVS_COMMIT=072586267e68ece9a47aa43f8c108e0dcbf44622
ARG OVS_SHA256=36d9c14036489b63de84ebace837fcacf7e60e669a0dc715802c5443684ea4dc
ARG CLAUDE_VERSION=2.1.277
ARG CLAUDE_VSIX_SHA256=e55b34b71c130b89f2fc0e48eca5bfe67fbd7576b539c8c3e359a73004f141b9
ARG CLAUDE_KEY_FPR=31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE
ARG AWSCLI_VERSION=2.37.5
ARG AWSCLI_SHA256=95d2bac9242185ae3a2970ec38c8766d3bf7b31609a0302dec3643522a676d32
ARG AWSCLI_KEY_FPR=FB5DB77FD5C118B80511ADA8A6310ACC4672475C
# Packages that openvscode-server 1.109.5 bundles, replaced with newer releases from the npm registry, each
# checked by its SHA-512. Drop an entry once an upstream openvscode-server release ships that version. ip-address
# 10.x is a major version, so socks (its only user, for VS Code's SOCKS proxy support) moves with it to a release
# built on 10.x (socks 2.8.7 and later).
ARG OVS_NODE_FIXES="\
shell-quote@1.9.0=sha512-Iov+JwFv/2HcTpcwNMKd8+IWNb8tboQJNQTkAY/LLVK7gGH9jy+LGkVqPxfekHl+yMmiqXszdGWXgkfml7hjqA== \
undici@7.29.1=sha512-RYONW2MeafgYlkVOKYKkA/Ag7BmXqgIWCa8t1m0JcxrQg9pI9lEqRhAOruOBCbAohOa/gkCF+iPi9hrgvTzu6Q== \
lodash-es@4.18.0=sha512-koAgswPPA+UTaPN64Etp+PGP+WT6oqOS2NMi5yDkMaiGw9qY4VxQbQF0mtKMyr4BlTznWyzePV5UpECTJQmSUA== \
picomatch@4.0.4=sha512-QP88BAKvMam/3NxH6vj2o21R6MjxZUAd6nlwAS/pnGvN9IVLocLHxGYIzFhg6fUQ+5th6P4dv4eW9jX3DSIj7A== \
socks@2.8.10=sha512-e0VyvkVTwVYViNovRkZ9aodhxVlyoMn7eJhVUPxZ+eK9P/7CBkxvvsBOHqFPEH416726W8tLXXXjKwqgTErrCQ== \
ip-address@10.7.3=sha512-A1kdq/tSb5QjvKvAMgIoEvDBIgL7qaqVP/jkvSwYYRZ9iEzvPpopxp2wQfu3SuZRHtpHNxMn8Fs0bS+gf5Xmwg=="
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg jq openssl unzip \
 && rm -rf /var/lib/apt/lists/*
COPY keys/aws-cli.asc proxy/package-lock.json /in/
WORKDIR /out
RUN set -eux; \
    ovs="https://github.com/gitpod-io/openvscode-server/releases/download/openvscode-server-v${OVS_VERSION}"; \
    curl -fsSL -o ovs.tgz "${ovs}/openvscode-server-v${OVS_VERSION}-linux-arm64.tar.gz"; \
    echo "${OVS_SHA256}  ovs.tgz" | sha256sum -c -; \
    mkdir ovs; tar -xzf ovs.tgz -C ovs --strip-components=1 --no-same-owner; rm ovs.tgz; \
    test "$(jq -r .commit ovs/product.json)" = "${OVS_COMMIT}"; \
    \
    vsix="https://open-vsx.org/api/Anthropic/claude-code/linux-arm64/${CLAUDE_VERSION}/file/Anthropic.claude-code-${CLAUDE_VERSION}@linux-arm64"; \
    curl -fsSL -o cc.vsix "${vsix}.vsix"; \
    curl -fsSL -o cc.sha256 "${vsix}.sha256"; \
    test "$(tr -d '[:space:]' < cc.sha256 | cut -c1-64)" = "${CLAUDE_VSIX_SHA256}"; \
    echo "${CLAUDE_VSIX_SHA256}  cc.vsix" | sha256sum -c -; \
    mkdir vsix; unzip -q cc.vsix -d vsix; rm cc.vsix cc.sha256; \
    test "$(jq -r '.publisher + "." + .name + "@" + .version' vsix/extension/package.json)" = "Anthropic.claude-code@${CLAUDE_VERSION}"; \
    \
    curl -fsSL -o claude-code.asc https://downloads.claude.ai/keys/claude-code.asc; \
    export GNUPGHOME="$(mktemp -d)"; \
    test "$(gpg --batch --with-colons --show-keys claude-code.asc | grep -c '^pub:')" = 1; \
    test "$(gpg --batch --with-colons --show-keys claude-code.asc | awk -F: '$1 == "fpr" {print $10; exit}')" = "${CLAUDE_KEY_FPR}"; \
    \
    aws="https://awscli.amazonaws.com/awscli-exe-linux-aarch64-${AWSCLI_VERSION}.zip"; \
    curl -fsSL -o awscli.zip "${aws}"; \
    curl -fsSL -o awscli.zip.sig "${aws}.sig"; \
    echo "${AWSCLI_SHA256}  awscli.zip" | sha256sum -c -; \
    gpg --batch --quiet --import /in/aws-cli.asc; \
    gpg --batch --status-fd 1 --verify awscli.zip.sig awscli.zip 2>/dev/null | grep -q "VALIDSIG ${AWSCLI_KEY_FPR}"; \
    unzip -q awscli.zip; rm awscli.zip awscli.zip.sig; \
    \
    test "$(jq -c '.packages | keys' /in/package-lock.json)" = '["","node_modules/ws"]'; \
    url="$(jq -r '.packages["node_modules/ws"].resolved' /in/package-lock.json)"; \
    sri="$(jq -r '.packages["node_modules/ws"].integrity' /in/package-lock.json)"; \
    [[ $url == https://registry.npmjs.org/ws/-/ws-*.tgz ]]; \
    curl -fsSL -o ws.tgz "${url}"; \
    test "sha512-$(openssl dgst -sha512 -binary ws.tgz | base64 -w0)" = "${sri}"; \
    mkdir -p node_modules/ws; tar -xzf ws.tgz -C node_modules/ws --strip-components=1 --no-same-owner; rm ws.tgz
RUN set -eux; \
    for fix in ${OVS_NODE_FIXES}; do \
      pkg="${fix%%@*}"; ver="${fix#*@}"; ver="${ver%%=*}"; sri="${fix#*=}"; \
      test -f "ovs/node_modules/${pkg}/package.json"; \
      curl -fsSL -o fix.tgz "https://registry.npmjs.org/${pkg}/-/${pkg}-${ver}.tgz"; \
      test "sha512-$(openssl dgst -sha512 -binary fix.tgz | base64 -w0)" = "${sri}"; \
      rm -rf "ovs/node_modules/${pkg}"; mkdir "ovs/node_modules/${pkg}"; \
      tar -xzf fix.tgz -C "ovs/node_modules/${pkg}" --strip-components=1 --no-same-owner; rm fix.tgz; \
      test "$(jq -r .version "ovs/node_modules/${pkg}/package.json")" = "${ver}"; \
    done

# ---- the box ---------------------------------------------------------------------------------
FROM --platform=linux/arm64 ${UBUNTU}
ARG CLAUDE_VERSION=2.1.277
ARG AWSCLI_VERSION=2.37.5
ARG BUILD_DATE=unknown
ARG SOURCE_REVISION=unknown
ENV LANG=C.UTF-8
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# One layer for everything big, so the image stays well under AgentCore's 2 GB and 53 layers.
RUN --mount=type=bind,from=fetch,source=/out,target=/fetch \
    --mount=type=bind,source=requirements-mcp-proxy.txt,target=/tmp/requirements-mcp-proxy.txt \
    set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl git tmux jq unzip python3 python3-venv \
      less openssh-client procps; \
    install -d -m 0755 /etc/apt/keyrings; \
    install -m 0644 /fetch/claude-code.asc /etc/apt/keyrings/claude-code.asc; \
    echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] https://downloads.claude.ai/claude-code/apt/stable stable main" \
      > /etc/apt/sources.list.d/claude-code.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends "claude-code=${CLAUDE_VERSION}-1"; \
    apt-mark hold claude-code; \
    apt-get clean; rm -rf /var/lib/apt/lists/*; \
    \
    cp -a /fetch/ovs /opt/openvscode-server; \
    # No extension gallery on the box, so neither the server CLI nor a server-side
    # install can reach open-vsx.org. commit, quality and version stay (the proxy, the supervisor
    # and build-info read them).
    jq 'del(.extensionsGallery)' /opt/openvscode-server/product.json > /tmp/product.json; \
    install -m 0644 /tmp/product.json /opt/openvscode-server/product.json; rm /tmp/product.json; \
    test "$(jq -c '[has("extensionsGallery"), (.commit | length), .quality]' /opt/openvscode-server/product.json)" = '[false,40,"stable"]'; \
    ext=/opt/openvscode-server/extensions/anthropic.claude-code; \
    cp -a /fetch/vsix/extension "${ext}"; \
    # The VSIX bundles its own copy of the same CLI build; one hard-linked copy saves ~230 MB.
    if cmp -s /usr/bin/claude "${ext}/resources/native-binary/claude"; then \
      ln -f /usr/bin/claude "${ext}/resources/native-binary/claude"; fi; \
    # The server's --extensions-dir: root-owned and read-only, holding the
    # extensions.json the server itself writes on first use, so the running server starts cleanly
    # and can't install or side-load anything. Claude Code is found as a system extension above.
    install -d -m 0755 /opt/devbox/extensions; \
    /opt/openvscode-server/bin/openvscode-server --server-data-dir /tmp/ovs-build \
      --extensions-dir /opt/devbox/extensions --list-extensions; \
    rm -rf /tmp/ovs-build; \
    test "$(jq -c . /opt/devbox/extensions/extensions.json)" = '[]'; \
    chmod -R a-w /opt/devbox/extensions; \
    \
    /fetch/aws/install -i /usr/local/aws-cli -b /usr/local/bin; \
    python3 -m venv /opt/mcp-proxy; \
    /opt/mcp-proxy/bin/pip install --no-cache-dir --disable-pip-version-check --require-hashes \
      --only-binary=:all: --no-deps -r /tmp/requirements-mcp-proxy.txt; \
    # pip is only needed to install the pinned set above.
    /opt/mcp-proxy/bin/python -m pip uninstall -y --disable-pip-version-check pip; \
    mkdir -p /opt/devbox/proxy; \
    cp -a /fetch/node_modules /opt/devbox/proxy/node_modules; \
    \
    # ubuntu:24.04 ships a user "ubuntu" with uid 1000.
    userdel -r ubuntu 2>/dev/null || true; \
    groupadd --gid 1000 dev; \
    useradd --uid 1000 --gid 1000 --home-dir /mnt/workspace/home --no-create-home --shell /bin/bash dev; \
    groupadd --system --gid 1001 devboxproxy; \
    useradd --system --uid 1001 --gid 1001 --home-dir /nonexistent --no-create-home \
      --shell /usr/sbin/nologin devboxproxy

COPY rootfs/ /
COPY proxy/package.json proxy/package-lock.json proxy/server.mjs /opt/devbox/proxy/
COPY proxy/lib/ /opt/devbox/proxy/lib/

RUN set -eux; \
    install -d -m 0755 /etc/devbox /etc/claude-code/managed-settings.d /run/devbox; \
    chown -R root:root /etc/claude-code /etc/devbox /opt /usr/local/bin; \
    chmod -R go-w /etc/claude-code /etc/devbox /opt /usr/local/bin; \
    find /etc/claude-code -type f -exec chmod 0644 {} +; \
    chmod 0755 /etc/claude-code/hooks/*.py /usr/local/bin/devbox-entrypoint /usr/local/bin/devbox-claude \
      /opt/devbox/lib/devbox/dev_helper.py; \
    # Nothing in the image may raise privileges: strip setuid/setgid from the base image too.
    find / -xdev -type f -perm /6000 -exec chmod ug-s {} +; \
    jq -n --arg built "${BUILD_DATE}" --arg rev "${SOURCE_REVISION}" --arg claude "${CLAUDE_VERSION}" \
      --arg awscli "${AWSCLI_VERSION}" \
      --arg ovs "$(jq -r .version /opt/openvscode-server/product.json)" \
      --arg commit "$(jq -r .commit /opt/openvscode-server/product.json)" \
      --arg vsix "$(jq -r .version /opt/openvscode-server/extensions/anthropic.claude-code/package.json)" \
      --arg ws "$(jq -r .version /opt/devbox/proxy/node_modules/ws/package.json)" \
      --arg mcp "$(/opt/mcp-proxy/bin/python -c 'import importlib.metadata as m; print(m.version("mcp-proxy-for-aws"))')" \
      '{built: $built, revision: $rev, openvscodeServer: $ovs, commit: $commit, claudeCode: $claude, \
        claudeVsix: $vsix, awsCli: $awscli, mcpProxyForAws: $mcp, ws: $ws}' > /opt/devbox/build-info.json

EXPOSE 8080
# CMD, not ENTRYPOINT: AgentCore Instances started the ENTRYPOINT-only image as "exited before
# becoming ready" with no output at all; the AWS Instances samples all start through CMD.
CMD ["/usr/local/bin/devbox-entrypoint"]
