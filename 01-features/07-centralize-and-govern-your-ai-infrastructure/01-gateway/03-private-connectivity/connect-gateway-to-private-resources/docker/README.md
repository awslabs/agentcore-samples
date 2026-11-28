# Existing MCP server packaging

The ECS stack uses these two contexts with `linux/amd64`:

| Context | Process | Port / MCP path |
| --- | --- | --- |
| `fastmcp-mock` | `python server.py` | 8000 / `/mcp` |
| `stock-mcp-mock` | `python server.py` | 8001 / `/stock-mcp/` |

The Dockerfiles run as UID 10001, install hash-locked binary wheels, and copy only
the existing application. The official Python 3.12.16 Bookworm **AMD64** base is
pinned by manifest digest; this does not select an ARM64 image. TLS remains on the internal ALB, not in these servers.
No model or external stock service is called; stock values are mock data.

From the parent sample directory, with a running Docker-compatible engine:

```bash
docker build --platform linux/amd64 -t vpc-egress-echo:local docker/fastmcp-mock
docker build --platform linux/amd64 -t vpc-egress-stock:local docker/stock-mcp-mock
```

Finch can run the same builds using `finch build`; do not start another VM if one
is already available. Use an empty private registry configuration for anonymous
builds. Never point offline tests at an AWS/ECR credential helper.

Start each image with **`--network none`**, no credential mounts and no host ports.
The checked-in probe exercises actual HTTP initialization, `tools/list`, valid
tool calls and an unknown-tool error. Run both cases from the parent sample:

```bash
set -euo pipefail
for SERVER in echo stock; do
  CONTEXT=fastmcp-mock
  [[ "$SERVER" == echo ]] || CONTEXT=stock-mcp-mock
  SHA=$(python3 -c "import hashlib; print(hashlib.sha256(open('docker/$CONTEXT/server.py','rb').read()).hexdigest())")
  CID=$(docker run -d --network none --cpus 2 --memory 512m --pids-limit 128 \
    --read-only --tmpfs /tmp:rw,nosuid,size=64m --cap-drop ALL \
    --security-opt no-new-privileges \
    -e HOME=/tmp -e XDG_CACHE_HOME=/tmp/.cache \
    -e AWS_ACCESS_KEY_ID=offline -e AWS_SECRET_ACCESS_KEY=offline \
    -e AWS_EC2_METADATA_DISABLED=true -e AWS_CONFIG_FILE=/dev/null \
    -e AWS_SHARED_CREDENTIALS_FILE=/dev/null \
    --mount "type=bind,src=$PWD/docker/check_mcp.py,dst=/checks/probe.py,readonly" \
    "vpc-egress-$SERVER:local")
  trap 'docker rm -f "$CID" >/dev/null' EXIT
  docker exec "$CID" python /checks/probe.py --server "$SERVER" --source-sha256 "$SHA"
  docker rm -f "$CID"
  trap - EXIT
  test -z "$(docker ps -aq --filter "id=$CID")"
done
```

Remove only exact owned containers/images, never globally prune. These are AMD64
ECS artifacts. Their success is not ARM64 Runtime validation or a cloud test.

## Reproducible dependencies and checks

From this directory:

```bash
# Requires uv 0.9.28. Ordinary regeneration preserves existing versions.
python3 lock_requirements.py
python3 lock_requirements.py --check
# Deliberate upgrades require new tests and an advisory review:
# python3 lock_requirements.py --upgrade
```

`requirements.in` pins FastMCP **4.2.5**. Generation uses universal platform
resolution and both context files must be byte-identical. PEP 508 markers retain
the selected SDK's conditional dependencies, including Windows-only `pywin32`,
without installing them on other platforms. The selected FastMCP graph no longer
uses the legacy diskcache/keyring chain. The lock tests check declared inputs,
hashes and consistent platform environments rather than freezing obsolete
transitive dependencies. Windows/PyPy execution is not claimed.

By default, the generator constrains against the current lock and refuses version
drift. `--upgrade` deliberately refreshes compatible dependencies; rebuild both
images, rerun their real HTTP probes, and review advisories before accepting it.
The probe retries server startup readiness only, never failed functional checks.

The local tests under `../vpcegress/tests/test_servers.py` load both real servers,
exercise their tools through FastMCP's in-process transport with sockets denied,
and verify each main entrypoint's port/path. These tests do **not** execute the
Dockerfile, Linux image or ARM64 binaries.

Migration evidence includes real 1.0.3 CodeZip dependency bundles of these sources
for Python 3.13/Linux ARM64. The nested CLI project itself declares no Runtime;
those are diagnostic ZIPs, not replacements for these ECS images. Run the exact
image procedure above independently of CodeZip and in-process tests. A successful
local image check does not verify ALB TLS, private VPC routing, Gateway auth or
any optional parent stack in AWS. **Cloud validation is a separate gated step.**
