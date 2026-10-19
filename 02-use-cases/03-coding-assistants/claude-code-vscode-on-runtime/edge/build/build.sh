#!/usr/bin/env bash
# Builds edge/dist/ (the static bundle baked into the edge Lambda image).
#
#   build/build.sh                                   download the pinned arm64 tarball, verify, build
#   DEVBOX_OVS_TARBALL=/path/to.tgz build/build.sh   use a local copy (still sha256-verified)
#
# Needs Node.js >= 22 and tar. Output: edge/dist/ (git-ignored), cache: edge/.cache/.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v node >/dev/null; then
  echo "build.sh: node is required (>= 22)" >&2
  exit 1
fi
major="$(node -p 'process.versions.node.split(".")[0]')"
if (( major < 22 )); then
  echo "build.sh: node >= 22 is required, found $(node --version)" >&2
  exit 1
fi

# zlib compression runs on libuv's thread pool; size it to the machine so brotli -11 runs in parallel.
cpus="$(node -p 'require("os").availableParallelism()')"
export UV_THREADPOOL_SIZE="${UV_THREADPOOL_SIZE:-$cpus}"

exec node "$here/build.mjs" "$@"
