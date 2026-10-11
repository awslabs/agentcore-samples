#!/usr/bin/env python3
"""Multi-model failover demo (offline: boto3/STS are mocked, no AWS needed).

Bedrock rate limits are per model, per account. Treating every
(model, account) pair as its own quota space turns 2 models x 2 accounts
into 4 independent quotas, and a throttle becomes a reason to move to the
next space instead of a user-visible error.

Four scenarios, each asserted (exit 0 = all behaviors verified):
  1. Account failover: same model, next account.
  2. Model failover: all accounts of the rank-1 model busy -> next model.
  3. Real errors propagate: no failover masking of genuine failures.
  4. All spaces exhausted (forced): clean error after the full walk.

Run:
    python examples/multi_model_failover_demo.py
"""

import asyncio
import logging
import sys
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from narrateai_qa.multi_model_failover import MultiModelBedrockModel

logging.basicConfig(level=logging.CRITICAL)  # demo prints its own narrative

SONNET = "global.anthropic.claude-sonnet-5-20250929-v1:0"
HAIKU = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

# 2 models x 2 accounts = 4 independent quota spaces.
# ARNs are placeholders; STS is mocked so nothing is called for real.
MODEL_CONFIGS = {
    1: {
        "model_id": SONNET,
        "region": "us-west-2",
        "client_kwargs": {
            "bedrock_role_arn_list": [
                "arn:aws:iam::account-a:role/BedrockCrossAccountRole",
                "arn:aws:iam::account-b:role/BedrockCrossAccountRole",
            ]
        },
    },
    2: {
        "model_id": HAIKU,
        "region": "us-west-2",
        "client_kwargs": {
            "bedrock_role_arn_list": [
                "arn:aws:iam::account-a:role/BedrockCrossAccountRole",
                "arn:aws:iam::account-b:role/BedrockCrossAccountRole",
            ]
        },
    },
}

THROTTLE_ERROR = ClientError(
    {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
    "ConverseStream",
)

FAKE_CREDS = {
    "Credentials": {
        "AccessKeyId": "ASIA-FAKE-FOR-DEMO",
        "SecretAccessKey": "fake-secret-for-demo",  # pragma: allowlist secret
        "SessionToken": "fake-token",
    }
}


def short(model_id: str) -> str:
    return "sonnet" if "sonnet" in model_id else "haiku "


def account(arn: str) -> str:
    return arn.split("::")[1].split(":")[0]


def build_model(attempt_log):
    """Construct MultiModelBedrockModel with STS and the Bedrock client mocked."""
    with patch("boto3.client") as mock_sts, patch("boto3.Session") as mock_session:
        mock_sts.return_value.assume_role.return_value = FAKE_CREDS
        mock_session.return_value = MagicMock()
        with patch.object(MultiModelBedrockModel, "_swap_to_model_and_account") as mock_swap:
            mock_swap.side_effect = lambda model_id, role_arn: attempt_log.append((model_id, role_arn))
            model = MultiModelBedrockModel(model_configs=MODEL_CONFIGS)
    model._swap_to_model_and_account = lambda model_id, role_arn: attempt_log.append((model_id, role_arn))
    return model


async def run_stream(model, throttle_first_n: int, error: Exception = THROTTLE_ERROR):
    """Drive model.stream() with a fake Bedrock that fails the first N calls."""
    call_count = {"n": 0}

    async def fake_stream(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= throttle_first_n:
            raise error
        yield {"contentBlockDelta": {"delta": {"text": "The answer"}}}
        yield {"contentBlockDelta": {"delta": {"text": " is 42."}}}

    events = []
    raised = None
    with patch.object(MultiModelBedrockModel.__bases__[0], "stream", fake_stream):
        try:
            async for event in model.stream(messages=[]):
                events.append(event)
        except Exception as e:  # demo inspects every outcome, incl. the all-exhausted raise
            raised = e
    return events, raised


def print_walk(attempts, failed_count):
    for i, (mid, arn) in enumerate(attempts, 1):
        outcome = "THROTTLED" if i <= failed_count else "OK -> streams to user"
        print(f"    attempt {i}:  {short(mid)} @ {account(arn)}   ...  {outcome}")


async def scenario_account_failover():
    print("  The rank-1 model throttles on one account. The retry stays on the")
    print("  SAME model but uses the NEXT account, a different quota space.")
    print()
    attempts = []
    model = build_model(attempts)
    attempts.clear()

    events, raised = await run_stream(model, throttle_first_n=1)
    print_walk(attempts, failed_count=1)
    print()
    print(
        f'  User experience: full answer ("{events[0]["contentBlockDelta"]["delta"]["text"]}'
        f'{events[1]["contentBlockDelta"]["delta"]["text"]}"), throttle never surfaced.'
    )

    assert raised is None
    assert len(attempts) == 2
    assert attempts[0][0] == attempts[1][0] == SONNET, "must stay on rank-1 model"
    assert attempts[0][1] != attempts[1][1], "must move to a different account"
    assert len(events) == 2
    return True


async def scenario_model_failover():
    print("  BOTH accounts of the rank-1 model are throttled. Only now does the")
    print("  failover descend to the rank-2 model: quality degrades last.")
    print()
    attempts = []
    model = build_model(attempts)
    attempts.clear()

    events, raised = await run_stream(model, throttle_first_n=2)
    print_walk(attempts, failed_count=2)
    print()
    print("  User experience: answer arrives from the next-best model.")

    assert raised is None
    assert len(attempts) == 3
    assert attempts[0][0] == attempts[1][0] == SONNET, "both sonnet accounts tried first"
    assert attempts[2][0] == HAIKU, "then descend to rank-2 model"
    assert len(events) == 2
    return True


async def scenario_real_error_propagates():
    print("  A ValidationException is not a capacity problem; retrying elsewhere")
    print("  would just mask a real bug. It must propagate immediately.")
    print()
    attempts = []
    model = build_model(attempts)
    attempts.clear()

    validation_error = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "bad request"}},
        "ConverseStream",
    )
    _, raised = await run_stream(model, throttle_first_n=99, error=validation_error)

    code = raised.response["Error"]["Code"] if isinstance(raised, ClientError) else None
    print(f"    attempt 1:  {short(attempts[0][0])} @ {account(attempts[0][1])}   ...  {code}")
    print()
    print("  User experience: sees the real error after 1 attempt (no futile walk).")

    assert len(attempts) == 1, "no failover on non-throttling errors"
    assert code == "ValidationException"
    return True


async def scenario_all_exhausted():
    print("  Forced worst case: ALL 4 quota spaces throttle. Failover multiplies")
    print("  capacity but can't create it. After walking the whole grid, the")
    print("  right behavior is a clean error, not an infinite retry loop.")
    print("  (Production runs 9 spaces; this case never occurred in 6 months.)")
    print()
    attempts = []
    model = build_model(attempts)
    attempts.clear()

    _, raised = await run_stream(model, throttle_first_n=99)
    print_walk(attempts, failed_count=4)
    print()
    print(f"  Raised after full walk: {str(raised)[:60]}...")

    assert len(attempts) == 4, "full 2x2 grid must be walked"
    assert raised is not None and "exhausted" in str(raised)
    return True


async def main():
    print("=" * 72)
    print("MULTI-MODEL FAILOVER DEMO (mocked Bedrock, no AWS needed)")
    print("=" * 72)
    print()
    print("  Setup: 2 models x 2 accounts = 4 independent quota spaces")
    print()
    print("             account-a   account-b")
    print("    sonnet   [quota 1]   [quota 2]    <- rank 1, tried first")
    print("    haiku    [quota 3]   [quota 4]    <- rank 2, quality fallback")
    print()
    print("  Account order is shuffled per request (prevents hot spotting),")
    print("  so which account goes first varies between runs.")

    scenarios = [
        ("SCENARIO 1/4: account failover (the common case)", scenario_account_failover),
        ("SCENARIO 2/4: model failover (rank-1 fully busy)", scenario_model_failover),
        ("SCENARIO 3/4: real errors are never masked", scenario_real_error_propagates),
        ("SCENARIO 4/4: every space exhausted (forced)", scenario_all_exhausted),
    ]

    results = []
    for name, fn in scenarios:
        print()
        print("-" * 72)
        print(name)
        print("-" * 72)
        try:
            results.append((name, await fn()))
        except AssertionError as e:
            print(f"  ASSERTION FAILED: {e}")
            results.append((name, False))

    print()
    print("=" * 72)
    print("INVARIANT CHECKS")
    print("=" * 72)
    all_passed = True
    for name, passed in results:
        print(f"  [{'ok' if passed else 'x '}] {name}")
        all_passed = all_passed and passed

    if not all_passed:
        print("\nDemo FAILED.")
        sys.exit(1)

    print("\nAll invariants hold. Demo PASSED.")


if __name__ == "__main__":
    asyncio.run(main())
