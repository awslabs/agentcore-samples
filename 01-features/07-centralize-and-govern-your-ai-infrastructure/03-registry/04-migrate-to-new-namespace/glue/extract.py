#!/usr/bin/env python3
"""Stage 1 of the migration: read Preview registry records into replayable staging.

This is one of the two files Glue runs. It is not the front door -- use the CLI:

    agent-registry-migration run

which calls this stage (and the transform/load stage) with the configuration you set up once.

Runs unchanged in two environments:

* **AWS Glue** -- Glue copies this file to the worker and installs ``migration_common`` from the
  wheel passed in ``--extra-py-files``, so the import below resolves to the installed package.
* **Plain Python** -- the CLI runs it this way, and it works from a clone with no install step.
  The bootstrap below puts the sibling ``common/`` directory on ``sys.path`` when
  ``migration_common`` is not already importable.

All logic lives in ``migration_common.jobs.extract``; this file only wires up the entrypoint.
"""

import os
import sys

try:  # pragma: no cover - exercised by the standalone smoke test
    import migration_common  # noqa: F401
except ModuleNotFoundError:
    _LIBRARY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "common")
    if os.path.isdir(_LIBRARY_DIR) and _LIBRARY_DIR not in sys.path:
        sys.path.insert(0, _LIBRARY_DIR)

from migration_common.jobs.extract import run

if __name__ == "__main__":
    run()
