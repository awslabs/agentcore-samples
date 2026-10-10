#!/usr/bin/env python3
"""Refresh the box source files embedded in explainer.html.

The explainer is one self-contained file: its file viewer shows these four files from a JSON block in the page,
not from disk. Run this after changing any of them, from the repo's root folder:

  python3 tools/embed_sources.py           rewrite the block in explainer.html
  python3 tools/embed_sources.py --check   exit 1 if the block is out of date (changes nothing)
"""

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / "explainer.html"
SOURCES = {  # the viewer's key: the file in this repo
    "dockerfile": "box/Dockerfile",
    "managed": "box/rootfs/etc/claude-code/managed-settings.json",
    "hook": "box/rootfs/etc/claude-code/hooks/block-direct-access.py",
    "config": "box/rootfs/opt/devbox/lib/devbox/config.py",
}
BLOCK = re.compile(r'(<script type="application/json" id="devbox-sources">)(.*?)(</script>)', re.DOTALL)

texts = {key: (ROOT / path).read_text() for key, path in SOURCES.items()}
# "</" can't appear inside a <script> element; "<\/" is the same string to JSON.
block = json.dumps(texts, indent=1).replace("</", "<\\/")

page = PAGE.read_text()
match = BLOCK.search(page)
if not match:
    sys.exit("explainer.html has no devbox-sources block")
if match.group(2).strip() == block:
    print("explainer.html: the embedded sources are up to date")
    sys.exit(0)
if "--check" in sys.argv:
    print("explainer.html: the embedded sources are out of date; run python3 tools/embed_sources.py")
    sys.exit(1)
page = page[: match.start(2)] + "\n" + block + "\n" + page[match.end(2) :]
PAGE.write_text(page)
print("explainer.html: embedded " + ", ".join(SOURCES.values()))
