"""The two migration stages: extract, then transform/load.

The logic lives here, inside the versioned ``migration_common`` wheel, so it ships as one
artifact. ``glue/extract.py`` and ``glue/transform_load.py`` are thin shims that only call
:func:`run` in the matching module, because Glue requires a script at an S3 ``scriptLocation``.
The CLI calls the same two modules, so a local run and a Glue run execute identical code.
"""
