"""Check local tutorial links and shell syntax without executing any commands."""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DocumentationContracts(unittest.TestCase):
    def test_tutorial_links_and_bash_syntax(self):
        checked = 0
        for file in ROOT.rglob("*.md"):
            if any(part in file.parts for part in ("node_modules", "dist", "concepts")):
                continue  # Reference concept pages are unchanged by this port.
            text = file.read_text()
            for link in re.findall(r"\]\(([^)]+)\)", text):
                if "://" in link or link.startswith("#"):
                    continue
                local = link.split("#")[0]
                if local:
                    self.assertTrue((file.parent / local).exists(), f"{file}: {link}")
            for snippet in re.findall(r"```bash\n(.*?)```", text, re.DOTALL):
                # Markdown blockquotes are not part of the copied shell command.
                snippet = "\n".join(
                    re.sub(r"^> ?", "", line) for line in snippet.splitlines()
                )
                # Conceptual examples use angle-bracket placeholders, not real IDs.
                # Normalize only those placeholders for syntax checking (never run).
                snippet = re.sub(r"<[A-Za-z][A-Za-z0-9_-]*>", "EXAMPLE_VALUE", snippet)
                result = subprocess.run(
                    ["bash", "-n"],
                    input=snippet,
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, f"{file}: {result.stderr}")
                checked += 1
        self.assertGreater(checked, 100)


if __name__ == "__main__":
    unittest.main()
