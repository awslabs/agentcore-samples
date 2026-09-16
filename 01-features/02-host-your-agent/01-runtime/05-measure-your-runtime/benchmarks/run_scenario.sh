#!/usr/bin/env bash
# Run one scale scenario across both AgentCore Runtime legs (zip, container)
# in sequence. Prod only — see 0_public/README.md for why this copy is scoped
# down from the original benchmark.
#
# The legs run STRICTLY one at a time, with a drain delay in between. They
# share the same account quotas, so overlapping them would make each leg's
# throttling depend on the other and the comparison would measure
# interference rather than the runtimes.
#
# Scenarios:
#   1  ramp until 5,000 accepted, no hold, rate depends on the leg:
#        agentcore-zip:  +25 units/s   (--rate 25  --per 1)   -- unthrottled at this pace
#        agentcore:      +400 units/min (--rate 400 --per 60) -- see below
#      -> account ceiling: how many sessions can the account build, and how
#         fast does the first request on each one come back? (cold start)
#      The container leg was slowed from the original +25 units/s (=1,500/min)
#      because that pace tripped the account's new-session throttle before
#      reaching 5,000; at 400/min it takes longer (~12.5 min to 5,000 vs
#      ~200s) but should clear the ceiling instead of stalling on throttled
#      attempts. The zip leg has not shown that problem, so it keeps the
#      original rate. Override either independently with SCEN1_RATE_ZIP /
#      SCEN1_PER_ZIP / SCEN1_RATE_CONTAINER / SCEN1_PER_CONTAINER.
#   2  build 100 units, then a WARM staircase across the whole fleet
#      -> how much traffic does a fleet serve? (fleet-level ceiling)
#
#      This is the original benchmark's scenario 3, renamed to 2 in this copy:
#      scenario 2 (400 units/min, unused here -- reused above for scenario 1's
#      new rate) and scenario 4 (per-unit capacity, dropped from this copy) are
#      gone, so 1 and 2 are the only numbers that exist now.
#
# Usage:
#   ./run_scenario.sh 1                 # scenario 1, both legs
#   ./run_scenario.sh 2                 # scenario 2, both legs
#   ./run_scenario.sh 1 agentcore-zip   # a single leg
#   RAMP_TARGET=100 ./run_scenario.sh 1 # a small rehearsal (do this first)
#
# IMAGE_SIZE / AGENTCORE_PLATFORM_VERSION: pick WHICH ARN to use out
# of several kept in .env at once, matching infrastructure/'s own flags of
# the same name (AGENTCORE_MANAGED_COMPUTE_VERSION still works as a
# deprecated alias for AGENTCORE_PLATFORM_VERSION). .env can hold one ARN
# per size/version combination you've deployed (see .env.example), named
# e.g. AGENTCORE_ARN_750MB_V2. When set, these two env vars select which one
# this run drives, falling back to a less specific name and finally to the
# plain AGENTCORE_ARN / AGENTCORE_ZIP_ARN if no size/version-specific one is
# set (see resolve_arn below for the exact fallback order):
#
#   IMAGE_SIZE=750mb AGENTCORE_PLATFORM_VERSION=V2 ./run_scenario.sh 1 agentcore
#     -> tries AGENTCORE_ARN_750MB_V2, then AGENTCORE_ARN_V2, then
#        AGENTCORE_ARN_750MB, then AGENTCORE_ARN
#   AGENTCORE_PLATFORM_VERSION=V1 ./run_scenario.sh 1 agentcore-zip
#     -> tries AGENTCORE_ZIP_ARN_V1, then AGENTCORE_ZIP_ARN
#
# The output filename also picks up the same suffix (e.g.
# results-scenario1-agentcore-750mb-V2.json), so runs against different
# sizes/versions never overwrite each other's results.
#
# Any extra args after the leg name go straight to load_ramp.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"

SCENARIO="${1:-}"
# Scenario 2 (warm / unit reuse) builds a SMALL, SYMMETRIC fleet — 100 units,
# which both legs can reach — and then measures how much warm traffic that
# fleet serves, climbing the rate until latency or errors say stop.
# Scenario 1 measures how fast a fleet can be BUILT; this one measures what
# it can SERVE.
SCEN2_TARGET=100
SCEN2_RPS_STEPS="25,50,100,200,400,800,1600"
# Scenario 1's ramp rate differs per leg (see run_leg): the zip leg has not
# shown account throttling at the original pace, but the container leg needs
# the slower 400/min pace to clear the account ceiling instead of stalling on
# throttled attempts. HOLD is scenario-level (same for every leg).
SCEN1_RATE_ZIP="${SCEN1_RATE_ZIP:-25}"
SCEN1_PER_ZIP="${SCEN1_PER_ZIP:-1}"
SCEN1_RATE_CONTAINER="${SCEN1_RATE_CONTAINER:-400}"
SCEN1_PER_CONTAINER="${SCEN1_PER_CONTAINER:-60}"
case "${SCENARIO}" in
  1) HOLD=0 ;;
  2) RATE=25; PER=1; HOLD=0 ;;
  *) echo "usage: $0 <1|2> [leg] [extra load_ramp.py args...]" >&2; exit 2 ;;
esac
shift

# An explicit leg as the next arg; otherwise both, in order.
LEGS=(agentcore-zip agentcore)
case "${1:-}" in
  agentcore-zip|agentcore) LEGS=("$1"); shift ;;
esac

if [ -f "${ENV_FILE}" ]; then
  echo "[scenario] loading ${ENV_FILE}"
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
else
  echo "[scenario] no env file at ${ENV_FILE} (copy .env.example to .env)" >&2
fi

# AGENTCORE_MANAGED_COMPUTE_VERSION is a deprecated alias for
# AGENTCORE_PLATFORM_VERSION (the name now matches the API's own
# `platformVersion` field) — honored for one release, with a warning.
if [ -n "${AGENTCORE_MANAGED_COMPUTE_VERSION:-}" ] && [ -z "${AGENTCORE_PLATFORM_VERSION:-}" ]; then
  echo "[scenario] AGENTCORE_MANAGED_COMPUTE_VERSION is deprecated; use AGENTCORE_PLATFORM_VERSION instead" >&2
  AGENTCORE_PLATFORM_VERSION="${AGENTCORE_MANAGED_COMPUTE_VERSION}"
fi

PY="${PYTHON:-}"
if [ -z "${PY}" ]; then
  if [ -x "${SCRIPT_DIR}/../.venv/bin/python" ]; then
    PY="${SCRIPT_DIR}/../.venv/bin/python"
  else
    PY="python3"
  fi
fi

if [ "${SCENARIO}" = "2" ]; then
  TARGET_UNITS="${RAMP_TARGET:-${SCEN2_TARGET}}"
  RAMP_RPS_STEPS="${RAMP_RPS_STEPS:-${SCEN2_RPS_STEPS}}"
  export RAMP_RPS_STEPS
else
  TARGET_UNITS="${RAMP_TARGET:-5000}"
fi

# ---- Bounding a leg that will never reach the target ----
# The ramp does not back off under rejection, so a leg pinned at its ceiling
# would keep firing attempts until --max-seconds without this. The primary
# stop is the stall guard: no new ACCEPTED unit for this long means the fleet
# stopped growing, whatever the cause.
RAMP_STALL_SECONDS="${RAMP_STALL_SECONDS:-60}"
# Backstop on total attempts, for the case where accepted creeps up just fast
# enough to keep resetting the stall timer. A floor is needed for small
# targets, where the multiplier alone would allow too few attempts.
RAMP_MAX_OFFERED="${RAMP_MAX_OFFERED:-$((TARGET_UNITS * 10))}"
if [ "${RAMP_MAX_OFFERED}" -lt 200 ]; then RAMP_MAX_OFFERED=200; fi
export RAMP_STALL_SECONDS RAMP_MAX_OFFERED
# Seconds between legs, to let the previous fleet fully drain out of the quotas.
LEG_DELAY="${LEG_DELAY:-180}"

if [ "${SCENARIO}" = "1" ]; then
  echo "[scenario] scenario 1: zip +${SCEN1_RATE_ZIP} units/${SCEN1_PER_ZIP}s, container +${SCEN1_RATE_CONTAINER} units/${SCEN1_PER_CONTAINER}s -> ${TARGET_UNITS} units each, hold ${HOLD}s"
else
  echo "[scenario] scenario ${SCENARIO}: +${RATE} units/${PER}s -> ${TARGET_UNITS} units, hold ${HOLD}s"
fi
if [ "${SCENARIO}" = "2" ]; then
  echo "[scenario] warm staircase: ${RAMP_RPS_STEPS} req/s, ${RAMP_STEP_SECONDS:-30}s per step"
  echo "[scenario] NOTE: AgentCore stops at its 200/s invoke quota (per agent," \
       "per account) regardless of fleet size. Compare per-unit rates and" \
       "latency at <=200/s."
fi
echo "[scenario] legs: ${LEGS[*]}  (delay ${LEG_DELAY}s between legs)"
echo "[scenario] stop guards: stall ${RAMP_STALL_SECONDS}s without a new accepted unit," \
     "max ${RAMP_MAX_OFFERED} attempts"

_has_run=0
status=0

# Try, in order: <base>_<suffix1>_<suffix2>, <base>_<suffix2>, <base>_<suffix1>,
# <base>. Prints "VARNAME=value" for the first one that is set and non-empty,
# and fails (empty output, exit 1) if none are. Suffixes may be empty strings,
# in which case that combination is skipped.
resolve_arn() {
  local base="$1" suffix1="${2:-}" suffix2="${3:-}"
  local -a candidates=()
  [ -n "${suffix1}" ] && [ -n "${suffix2}" ] && candidates+=("${base}_${suffix1}_${suffix2}")
  [ -n "${suffix2}" ] && candidates+=("${base}_${suffix2}")
  [ -n "${suffix1}" ] && candidates+=("${base}_${suffix1}")
  candidates+=("${base}")
  local varname value
  for varname in "${candidates[@]}"; do
    value="${!varname:-}"
    if [ -n "${value}" ]; then
      echo "${varname}=${value}"
      return 0
    fi
  done
  return 1
}

run_leg() {
  local leg="$1"; shift
  # Scenario 1's rate is per-leg (see the SCEN1_* comment above); every other
  # scenario uses the scenario-level RATE/PER set in the case statement.
  local leg_rate="${RATE:-}" leg_per="${PER:-}"
  if [ "${SCENARIO}" = "1" ]; then
    case "${leg}" in
      agentcore-zip) leg_rate="${SCEN1_RATE_ZIP}"; leg_per="${SCEN1_PER_ZIP}" ;;
      agentcore)     leg_rate="${SCEN1_RATE_CONTAINER}"; leg_per="${SCEN1_PER_CONTAINER}" ;;
    esac
  fi
  # Uppercased so AGENTCORE_PLATFORM_VERSION=v1/v2 (any case) still
  # matches the .env variable names, which are always upper (AGENTCORE_ARN_*_V1).
  local version=""
  [ -n "${AGENTCORE_PLATFORM_VERSION:-}" ] && \
    version="$(printf '%s' "${AGENTCORE_PLATFORM_VERSION}" | tr '[:lower:]' '[:upper:]')"
  local size_upper=""
  [ "${leg}" = "agentcore" ] && [ -n "${IMAGE_SIZE:-}" ] && \
    size_upper="$(printf '%s' "${IMAGE_SIZE}" | tr '[:lower:]' '[:upper:]')"

  local leg_target="${TARGET_UNITS}"
  local out_suffix=""
  [ -n "${size_upper}" ] && out_suffix="${out_suffix}-${IMAGE_SIZE}"
  [ -n "${version}" ] && out_suffix="${out_suffix}-${version}"
  local out="results-scenario${SCENARIO}-${leg}${out_suffix}.json"

  local resolved arn_var arn_val plain_var
  case "${leg}" in
    agentcore)
      plain_var="AGENTCORE_ARN"
      resolved="$(resolve_arn "${plain_var}" "${size_upper}" "${version}")" || {
        echo "[scenario] skip ${leg}: no ${plain_var}[_${size_upper:-SIZE}][_${version:-VERSION}] set in .env" >&2
        return 1
      } ;;
    agentcore-zip)
      plain_var="AGENTCORE_ZIP_ARN"
      resolved="$(resolve_arn "${plain_var}" "${version}")" || {
        echo "[scenario] skip ${leg}: no ${plain_var}[_${version:-VERSION}] set in .env" >&2
        return 1
      } ;;
  esac
  arn_var="${resolved%%=*}"
  arn_val="${resolved#*=}"
  echo "[scenario] ${leg}: using \$${arn_var}"

  if [ "${_has_run}" -eq 1 ] && [ "${LEG_DELAY}" -gt 0 ]; then
    echo ""
    echo "[scenario] waiting ${LEG_DELAY}s for the previous fleet to drain..."
    sleep "${LEG_DELAY}"
  fi
  _has_run=1

  echo ""
  echo "==================== scenario ${SCENARIO}: ${leg} (rate +${leg_rate}/${leg_per}s) ===================="
  env "${plain_var}=${arn_val}" TARGET="${leg}" OUT="${out}" \
    "${PY}" "${SCRIPT_DIR}/load_ramp.py" \
    --rate "${leg_rate}" --per "${leg_per}" --target "${leg_target}" \
    --hold "${HOLD}" "$@"
}

for leg in "${LEGS[@]}"; do
  run_leg "${leg}" "$@" || status=1
done

echo ""
echo "[scenario] done. results: results-scenario${SCENARIO}-*.json"
# Non-zero if any leg was skipped or stopped short of the target. A stall is NOT
# a broken run — the stall point is the result — so read stop_reason and
# stop_detail in the JSON rather than treating this as a failure.
if [ "${status}" -ne 0 ]; then
  echo "[scenario] at least one leg did not reach ${TARGET_UNITS}; see stop_reason" >&2
fi
exit "${status}"
