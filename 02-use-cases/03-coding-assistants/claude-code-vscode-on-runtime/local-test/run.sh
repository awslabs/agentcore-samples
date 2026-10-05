#!/usr/bin/env bash
# The whole stack on this laptop, no AWS.
#
#   ./run.sh up      render generated/, build the edge assets and the images, start everything, wait until healthy
#   ./run.sh down    stop the stack and delete its workspace volume
#   ./run.sh e2e     fresh stack, then the headless Chrome end-to-end test (KEEP=1 leaves the stack up)
#   ./run.sh logs    follow the logs of every service
#   ./run.sh smoke   fakes + a stub box, then a browserless check (no box or edge build needed)
#   ./run.sh test    unit tests of the fakes (no Docker)
#
# Knobs: FAKE_AC_COLD_SECONDS (8), FAKE_AC_WS_MAX_SECONDS (3600; e2e uses 20), DEVBOX_TEST_MOUNT_DELAY (5),
# SKIP_EDGE_BUILD=1, EDGE_LOCAL_MODE=auto|local-server|cloudfront-sim, OKTA_PORT/AGENTCORE_PORT/WORKBENCH_PORT/WEBVIEW_PORT.
# Written for macOS's bash 3.2.
set -euo pipefail
cd "$(dirname "$0")"

GEN=generated
PORTS_FILE="$GEN/ports.env"
COMPOSE_FILES=(-f compose.yaml)

die() { echo "run.sh: $*" >&2; exit 1; }
compose() { docker compose "${COMPOSE_FILES[@]}" "$@"; }

need_tools() {
  command -v docker >/dev/null || die "docker is required (Docker Desktop)"
  docker info >/dev/null 2>&1 || die "Docker is not running"
  command -v node >/dev/null || die "node >= 22 is required"
  local major
  major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$major" -ge 22 ] || die "node >= 22 is required, found $(node --version)"
}

port_free() {
  node -e "require('net').createServer().once('error',()=>process.exit(1)).listen($1,'127.0.0.1',function(){this.close(()=>process.exit(0))})"
}

stack_running() { [ -n "$(compose ps -q 2>/dev/null)" ]; }

# Use the default ports when free. A port that is taken (on some laptops a VPN client holds 9400) moves to the
# first free one in 9404-9419, and the choice is kept in generated/ports.env for down/logs/e2e.
pick_ports() {
  mkdir -p "$GEN"
  if stack_running && [ -f "$PORTS_FILE" ]; then
    # shellcheck disable=SC1090
    . "$PORTS_FILE"
    export OKTA_PORT AGENTCORE_PORT WORKBENCH_PORT WEBVIEW_PORT
    return
  fi
  local taken="" name default chosen p
  for spec in OKTA_PORT:9400 AGENTCORE_PORT:9401 WORKBENCH_PORT:9402 WEBVIEW_PORT:9403; do
    name="${spec%%:*}"
    default="${spec##*:}"
    chosen="$(eval echo "\${$name:-}")"
    if [ -n "$chosen" ]; then
      port_free "$chosen" || die "$name=$chosen is in use"
    elif port_free "$default"; then
      chosen="$default"
    else
      for p in $(seq 9404 9419); do
        case " $taken " in *" $p "*) continue ;; esac
        if port_free "$p"; then chosen="$p"; break; fi
      done
      [ -n "$chosen" ] || die "no free port for $name in 9404-9419"
      echo "run.sh: port $default is in use on this machine; using $chosen for $name"
    fi
    taken="$taken $chosen"
    eval "export $name=$chosen"
  done
  printf 'OKTA_PORT=%s\nAGENTCORE_PORT=%s\nWORKBENCH_PORT=%s\nWEBVIEW_PORT=%s\n' \
    "$OKTA_PORT" "$AGENTCORE_PORT" "$WORKBENCH_PORT" "$WEBVIEW_PORT" > "$PORTS_FILE"
}

load_ports() {
  if [ -f "$PORTS_FILE" ]; then
    # shellcheck disable=SC1090
    . "$PORTS_FILE"
    export OKTA_PORT AGENTCORE_PORT WORKBENCH_PORT WEBVIEW_PORT
  fi
}

render() {
  node config/stack.mjs render "$GEN" >/dev/null
}

build_edge() {
  [ "${SKIP_EDGE_BUILD:-0}" = 1 ] && return 0
  [ -f ../edge/build/build.sh ] || die "../edge/build/build.sh not found: the edge component is not there yet"
  echo "run.sh: building the edge assets (log: $GEN/edge-build.log)"
  bash ../edge/build/build.sh > "$GEN/edge-build.log" 2>&1 || { tail -30 "$GEN/edge-build.log"; die "the edge build failed"; }
}

wait_healthy() {
  local services="$1" timeout="$2" deadline bad s line
  deadline=$((SECONDS + timeout))
  while :; do
    bad=""
    for s in $services; do
      line="$(compose ps -a --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null | awk -v s="$s" '$1 == s')"
      case "$line" in
        *" running healthy"|*" running ") ;;
        *) bad="$bad ${s}(${line#* })" ;;
      esac
    done
    [ -z "$bad" ] && return 0
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "run.sh: not healthy after ${timeout}s:$bad" >&2
      return 1
    fi
    sleep 2
  done
}

print_urls() {
  echo "run.sh: stack is up"
  echo "  workbench      http://localhost:$WORKBENCH_PORT/   (signs in as \${FAKE_OKTA_USER:-ada})"
  echo "  webview site   http://localhost:$WEBVIEW_PORT/"
  echo "  fake Okta      http://localhost:$OKTA_PORT/oauth2/default"
  echo "  fake AgentCore http://localhost:$AGENTCORE_PORT   (stats: /_fake/stats)"
}

cmd_up() {
  need_tools
  pick_ports
  render
  [ -f ../box/Dockerfile ] || die "../box/Dockerfile not found: the box component is not there yet"
  build_edge
  echo "run.sh: building images (log: $GEN/compose-build.log)"
  compose build > "$GEN/compose-build.log" 2>&1 || { tail -40 "$GEN/compose-build.log"; die "image build failed"; }
  compose up -d --remove-orphans
  wait_healthy "okta agentcore box edge" "${UP_TIMEOUT:-300}" || { compose ps -a; compose logs --tail=60; die "the stack did not come up"; }
  print_urls
}

cmd_down() {
  load_ports
  compose down -v --remove-orphans
}

cmd_logs() {
  load_ports
  compose logs -f --tail=200
}

ensure_e2e_deps() {
  if [ ! -d e2e/node_modules/puppeteer-core ]; then
    (cd e2e && npm ci --no-audit --no-fund)
  fi
}

cmd_e2e() {
  need_tools
  ensure_e2e_deps
  # A short maximum connection duration, so the forced-cutoff step sees AgentCore's hourly cut within the run.
  export FAKE_AC_WS_MAX_SECONDS="${FAKE_AC_WS_MAX_SECONDS:-20}"
  if stack_running; then compose down -v --remove-orphans; fi
  cmd_up
  mkdir -p e2e/artifacts
  local status=0
  (cd e2e && node --test --test-concurrency=1 e2e.test.mjs) || status=$?
  compose logs --no-color > e2e/artifacts/stack.log 2>&1 || true
  curl -fsS "http://localhost:$AGENTCORE_PORT/_fake/stats" > e2e/artifacts/agentcore-stats.json 2>/dev/null || true
  if [ "${KEEP:-0}" = 1 ]; then
    echo "run.sh: KEEP=1, leaving the stack running (./run.sh down to stop it)"
  else
    compose down -v --remove-orphans
  fi
  [ "$status" -eq 0 ] && echo "run.sh: e2e passed" || echo "run.sh: e2e FAILED (screenshots and logs in e2e/artifacts/)"
  return "$status"
}

cmd_smoke() {
  need_tools
  COMPOSE_FILES=(-f compose.yaml -f compose.stub-box.yaml)
  pick_ports
  render
  [ -d fake-agentcore/node_modules/ws ] || (cd fake-agentcore && npm ci --no-audit --no-fund)
  compose build okta agentcore > "$GEN/compose-build.log" 2>&1 || { tail -40 "$GEN/compose-build.log"; die "image build failed"; }
  compose up -d --remove-orphans okta agentcore box
  local status=0
  wait_healthy "okta agentcore box" 120 && node e2e/smoke.mjs || status=$?
  [ "$status" -eq 0 ] || compose logs --tail=80
  if [ "${KEEP:-0}" != 1 ]; then compose down -v --remove-orphans; fi
  return "$status"
}

cmd_test() {
  [ -d fake-agentcore/node_modules/ws ] || (cd fake-agentcore && npm ci --no-audit --no-fund)
  local status=0
  for d in fake-okta fake-agentcore edge-local; do
    echo "== $d"
    (cd "$d" && node --test test/*.test.mjs) || status=1
  done
  return "$status"
}

case "${1:-}" in
  up) cmd_up ;;
  down) cmd_down ;;
  e2e) cmd_e2e ;;
  logs) cmd_logs ;;
  smoke) cmd_smoke ;;
  test) cmd_test ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
