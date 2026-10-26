#!/bin/zsh
# ws-probe.sh <user> [--token-file FILE] [--arn ARN] [--workbench-domain DOMAIN]: ask AgentCore how it
# answers the dev box's WebSocket handshake, which a browser can't show (it only ever reports close code
# 1006). Also asks the box (op: diag) what reached it.
#
# Without --token-file, copy your token in the dev box tab's JavaScript console first:
#   copy(__devbox.getToken())
# The token is then read from the clipboard, the clipboard is cleared, and the token is never printed.
# With --token-file, the token is read from that file instead (clipboard untouched).
#
# By default the runtime ARN and workbench domain are looked up from deploy/.state.json by <user>, and
# that file must exist. Pass --arn (and optionally --workbench-domain) to use those directly instead,
# which skips reading .state.json entirely (useful when it's stale or missing).
#
# Examples:
#   ws-probe.sh grace
#   ws-probe.sh grace --token-file /path/to/token.txt
#   ws-probe.sh grace --token-file ada-token.txt --arn arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/foo-abc123
set -u
HERE=${0:A:h}
USAGE="usage: ws-probe.sh <user> [--token-file FILE] [--arn ARN] [--workbench-domain DOMAIN]   (after copy(__devbox.getToken()) in the dev box tab)"
USER_NAME=${1:?$USAGE}
shift
TOKEN_FILE= ARN_OVERRIDE= WB_OVERRIDE=
while (( $# )); do
  case $1 in
    --token-file) TOKEN_FILE=${2:?$USAGE}; shift 2 ;;
    --arn) ARN_OVERRIDE=${2:?$USAGE}; shift 2 ;;
    --workbench-domain) WB_OVERRIDE=${2:?$USAGE}; shift 2 ;;
    *) print $USAGE; exit 1 ;;
  esac
done
if [[ -n $TOKEN_FILE ]]; then
  T=$(<$TOKEN_FILE)
else
  T=$(pbpaste); pbcopy </dev/null
fi
[[ $T == ey*.*.* ]] || { print "${TOKEN_FILE:-The clipboard} doesn't hold a token. Run copy(__devbox.getToken()) in the dev box tab first, or pass --token-file with a file containing the token."; exit 1; }

if [[ -n $ARN_OVERRIDE ]]; then
  ARN=$ARN_OVERRIDE
  WB=${WB_OVERRIDE:--}
  SID=$(python3 -c '
import base64, hashlib, json, sys
payload = json.loads(base64.urlsafe_b64decode(sys.argv[1].split(".")[1] + "=="))
print("dbx-" + hashlib.sha256((payload["uid"] + ":manual").encode()).hexdigest())
' "$T")
else
  read -r ARN SID WB <<<"$(python3 - "$HERE/.state.json" "$USER_NAME" "$T" <<'EOF'
import base64, hashlib, json, sys
state, name, token = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
box = state.get("boxes", {}).get(name) or {}
if box.get("runtimeArn") and box.get("compute") == "microvm":
    payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    wb = (state.get("edge") or {}).get("workbenchDomain") or state.get("lastWorkbenchDomain") or "-"
    print(box["runtimeArn"], "dbx-" + hashlib.sha256(f"{payload['uid']}:{box['generation']}".encode()).hexdigest(), wb)
EOF
)"
  [[ -n $ARN ]] || { print "No microVM box for $USER_NAME in $HERE/.state.json: run \`uv run deploy/devbox.py deploy\` first, or pass --arn directly"; exit 1; }
fi
ENC=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$ARN")
BASE="https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/$ENC"
PATHQ=$(python3 -c 'import base64; print(base64.urlsafe_b64encode(b"/stable-072586267e68ece9a47aa43f8c108e0dcbf44622?reconnectionToken=00000000-0000-4000-8000-000000000000&reconnection=false&skipWebSocketFrames=false").decode().rstrip("="))')
SUB="base64UrlBearerAuthorization.$(print -rn -- "$T" | base64 | tr '+/' '-_' | tr -d '=\n')"
print "runtime $ARN\nsession $SID\n"

handshake() {  # handshake <label> <query suffix> <curl auth args...>
  local label=$1 q=$2; shift 2
  print "== $label"
  curl -s -i -N --http1.1 -m 8 "$BASE/ws?qualifier=DEFAULT&X-Amzn-Bedrock-AgentCore-Runtime-Session-Id=$SID$q" \
    -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" \
    -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" ${ORIGIN:+-H "Origin: $ORIGIN"} "$@" 2>&1 \
    | tr -d '\r' | awk 'NR==1 || tolower($0) ~ /^(x-amzn-error|sec-websocket-protocol)/ {print; next} body {print} /^$/ {body=1}' \
    | sed -E 's/(base64UrlBearerAuthorization)\.[A-Za-z0-9_-]+/\1.<token>/g' | head -8   # never print the token, even if echoed
  print
}
ORIGIN=
handshake "0. Authorization header, no VS Code path, NO Origin header" "" -H "Authorization: Bearer $T"
handshake "0b. the browser's way, NO Origin header" "&X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath=$PATHQ" -H "Sec-WebSocket-Protocol: $SUB, base64UrlBearerAuthorization"
if [[ -n $WB && $WB != "-" ]]; then
  ORIGIN=https://$WB     # the workbench distribution's origin, as the browser sends it
  handshake "1. Authorization header, no VS Code path" "" -H "Authorization: Bearer $T"
  handshake "2. Authorization header + the VS Code path parameter" "&X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath=$PATHQ" -H "Authorization: Bearer $T"
  handshake "3. the browser's way: token in Sec-WebSocket-Protocol + the path parameter" "&X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath=$PATHQ" \
    -H "Sec-WebSocket-Protocol: $SUB, base64UrlBearerAuthorization"
else
  print "(no workbench domain known — skipping the Origin-header variants; pass --workbench-domain to run them)\n"
fi

print "== 4. op: diag (what reached the box; header names only)"
curl -s -m 30 -X POST "$BASE/invocations?qualifier=DEFAULT" -H "Authorization: Bearer $T" \
  -H "Content-Type: application/json" -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $SID" -d '{"v":1,"op":"diag"}' \
  | python3 -c 'import json, sys
d = json.load(sys.stdin)
print(json.dumps({k: d.get(k) for k in ("ok", "error", "headersSeen", "websockets") if k in d} or d, indent=1)[:3000])'
unset T SUB
