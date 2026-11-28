"""Local receipts from successful creates. Never infer ownership from a name."""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

CONFIG = Path(__file__).with_name("runtime_config.json")


@contextmanager
def exclusive_operation():
    lock = CONFIG.with_suffix(".lock")
    try:
        lock.touch(exist_ok=False)
    except FileExistsError:
        raise SystemExit(f"Deploy or cleanup already in progress. Check {lock.name} before retrying.") from None
    try:
        yield
    finally:
        lock.unlink()


def reserve(state):
    try:
        with CONFIG.open("x") as stream:
            json.dump(state, stream, indent=2)
    except FileExistsError:
        raise SystemExit("runtime_config.json already exists. Reconcile the previous deployment first.") from None


def save(state):
    # A reader sees the old or new receipt, never a half-written RoleId.
    with NamedTemporaryFile("w", dir=CONFIG.parent, delete=False) as stream:
        json.dump(state, stream, indent=2)
    os.replace(stream.name, CONFIG)


@contextmanager
def creating(state, resource):
    # If AWS accepted a create but the client never got its response, there
    # is no trustworthy receipt. Leave the pending marker for manual review.
    state["pending"] = resource
    save(state)
    yield
    del state["pending"]
    save(state)
