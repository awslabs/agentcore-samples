#!/usr/bin/env python3
"""
Dependency-free ``.env`` loader for the script (CLI) path.

Why this exists: the notebooks call ``init_aws()``, which loads ``.env`` as a
side effect, so the notebook path has always worked. The **scripts** documented
as ``python setup_okta.py`` never loaded ``.env`` at all — they read
``os.environ`` directly, so the documented CLI path only worked if the reader had
already exported the variables by hand. A harness-driven or CI deploy takes the
script path, which made this a real gap rather than a cosmetic one.

``python-dotenv`` is deliberately NOT used: it is not a declared dependency of
this sample, and the one script that imported it (``utils/check_runtime.py``) was
relying on an undeclared import that happens to be present in some environments.
The parser below is ~15 lines, so removing the dependency is cheaper than pinning
it (and DR-19's lesson is that every unpinned dependency is a future breakage).

Semantics match ``python-dotenv``'s default: **an already-exported environment
variable wins.** ``.env`` supplies values that are missing, it never overrides
the shell. That precedence matters — it is what lets an operator override one
value for a single run without editing the file.

Note the deliberate contrast with ``aws_session_utils.load_env_credentials``,
which **overwrites** ``os.environ`` and is AWS-credential-specific (it reports
failure when a ``.env`` holds no AWS keys). That function keeps its behaviour for
the notebook credential-refresh flow; this one is the general-purpose loader for
config such as ``OKTA_ORG_URL`` / ``OKTA_API_TOKEN``.

Usage from a deployment script::

    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
    from utils.env_file import load_env_file

    load_env_file()
"""

import os
from pathlib import Path

# Never echo the value of a variable whose name looks like a credential.
_SECRET_HINTS = ("SECRET", "TOKEN", "PASSWORD", "KEY", "CREDENTIAL")


def find_env_file(start: Path | None = None) -> Path | None:
    """
    Locate the ``.env`` to use, searching upward from ``start`` (default: cwd).

    Scripts in this sample run with a variety of working directories — the
    notebooks run from the sample root, while the deployment scripts run from
    ``deployment/<step>/`` — so a plain ``Path.cwd() / ".env"`` finds the file for
    one caller and misses it for the other. Searching upward means the same call
    works from anywhere in the tree, and the sample root (where ``.env.example``
    lives, and therefore where readers put ``.env``) is always on the path.

    Args:
        start: Directory to begin the search from. Defaults to the current
            working directory.

    The **nearest** ``.env`` wins, which is the conventional rule but has one
    consequence worth knowing: if a ``.env`` exists both at the sample root and in
    ``deployment/``, a script under ``deployment/<step>/`` reads the latter while a
    notebook run from the sample root reads the former. The loader always prints
    the path it used, so a divergence is visible rather than silent.

    Upward search alone is NOT sufficient, which is easy to get wrong. "Upward"
    means toward the filesystem root, so a notebook running at the sample root
    cannot reach a ``.env`` that lives *below* it in ``deployment/`` — the walk
    goes the other way. Since the scripts run from ``deployment/<step>/`` and the
    notebooks run from the sample root, a purely-upward search serves the scripts
    and silently fails the notebooks. The upward walk is therefore followed by a
    bounded set of sample-root-relative candidates. The list is explicit rather
    than a recursive glob so resolution stays predictable.

    Args:
        start: Directory to begin the search from. Defaults to the current
            working directory.

    Returns:
        The first ``.env`` found, or None when none of the candidates exist.
    """
    start_dir = (start or Path.cwd()).resolve()
    for directory in (start_dir, *start_dir.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate

    # Nothing at or above the caller. Fall back to a bounded, explicit set of
    # locations relative to the sample root, derived from this file's own path so
    # an odd working directory cannot hide the reader's .env.
    for candidate in sample_root_env_candidates():
        if candidate.is_file():
            return candidate
    return None


def sample_root_env_candidates() -> list[Path]:
    """
    Ordered ``.env`` locations relative to the sample root, nearest-to-root first.

    ``.env.example`` ships at the sample root, so a reader following the README
    creates ``<sample root>/.env``. An operator working inside the deployment tree
    tends to create ``deployment/.env`` (or one per step) instead. Both layouts are
    supported, and the resolved path is always printed so it is obvious which file
    a given run actually used.
    """
    sample_root = Path(__file__).resolve().parent.parent
    candidates = [sample_root / ".env", sample_root / "deployment" / ".env"]
    deployment_dir = sample_root / "deployment"
    if deployment_dir.is_dir():
        candidates.extend(sorted(deployment_dir.glob("*/.env")))
    return candidates


def load_env_file(
    path: Path | None = None,
    start: Path | None = None,
    verbose: bool = True,
) -> tuple[Path | None, list[str]]:
    """
    Load ``.env`` into ``os.environ`` without overriding existing variables.

    Blank lines and ``#`` comments are skipped, as is any line without ``=``. A
    surrounding pair of single or double quotes is stripped from the value. An
    ``export `` prefix is tolerated so a file that doubles as a shell snippet
    still parses.

    Args:
        path: Explicit ``.env`` path. When omitted, ``find_env_file`` is used.
        start: Directory to search upward from when ``path`` is omitted.
        verbose: If True, print which file was used and which names were set.
            Values are never printed.

    Returns:
        ``(path_used, names_set)``. ``path_used`` is None when no file was found;
        ``names_set`` lists only the variables this call actually set, so an empty
        list means "the file added nothing" rather than "the file was missing" —
        the two are distinguishable by the first element.
    """
    env_path = path if path is not None else find_env_file(start)

    if env_path is None or not Path(env_path).is_file():
        if verbose:
            print("ℹ️  No .env found — using the exported environment as-is.")
        return None, []

    env_path = Path(env_path)
    names_set: list[str] = []
    skipped_existing: list[str] = []

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip("\"'")
        if key in os.environ:
            # dotenv semantics: the already-exported value wins.
            skipped_existing.append(key)
            continue
        os.environ[key] = value
        names_set.append(key)

    if verbose:
        print(f"✅ Loaded {env_path} ({len(names_set)} variable(s) set)")
        others = [c for c in sample_root_env_candidates() if c.is_file() and c != env_path.resolve()]
        if others:
            print(
                f"   ⚠️  {len(others)} other .env file(s) exist and were NOT read: {', '.join(str(o) for o in others)}"
            )
        for key in names_set:
            if any(hint in key.upper() for hint in _SECRET_HINTS):
                print(f"   {key} = ****** ({len(os.environ[key])} chars; value not shown)")
            else:
                print(f"   {key} = {os.environ[key]}")
        if skipped_existing:
            print(
                f"   {len(skipped_existing)} already set in the environment, "
                f"left unchanged: {', '.join(skipped_existing)}"
            )

    return env_path, names_set
