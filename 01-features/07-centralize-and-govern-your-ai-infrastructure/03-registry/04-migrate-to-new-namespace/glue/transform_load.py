#!/usr/bin/env python3
"""Stage 2 of the migration: transform staged Preview records and load them into the target registry.

This is one of the two files Glue runs. It is not the front door -- use the CLI:

    agent-registry-migration run           # dry run: transform and report, write nothing
    agent-registry-migration run --live    # create the target records

Runs unchanged in two environments:

* **AWS Glue** -- Glue copies this file to the worker and installs ``migration_common`` from the
  wheel passed in ``--extra-py-files``, so the import below resolves to the installed package.
* **Plain Python** -- the CLI runs it this way, and it works from a clone with no install step.
  The bootstrap below puts the sibling ``common/`` directory on ``sys.path`` when
  ``migration_common`` is not already importable.

All logic lives in ``migration_common.jobs.transform_load``; this file only wires up the
entrypoint.
"""

import os
import sys

try:  # pragma: no cover - exercised by the standalone smoke test
    import migration_common  # noqa: F401
except ModuleNotFoundError:
    _LIBRARY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "common")
    if os.path.isdir(_LIBRARY_DIR) and _LIBRARY_DIR not in sys.path:
        sys.path.insert(0, _LIBRARY_DIR)

from migration_common.jobs.transform_load import run

if __name__ == "__main__":
    run()
