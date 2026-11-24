"""Hash-lock regression: platform-conditional auth dependencies must not vanish."""
from pathlib import Path
import re
import unittest

from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]


class DependencyLockContracts(unittest.TestCase):
    def test_identical_hash_locked_contexts_and_platform_markers(self):
        text = (ROOT / 'docker/fastmcp-mock/requirements.txt').read_text()
        self.assertEqual(text, (ROOT / 'docker/stock-mcp-mock/requirements.txt').read_text())
        entries = {}
        for block in re.findall(r'(?m)^([A-Za-z0-9][^\n]*\n(?:[ \t]+[^\n]*\n)*)', text):
            line = block.splitlines()[0].rstrip(' \\')
            req = Requirement(line)
            self.assertRegex(str(req.specifier), r'^==[^*]+$')
            self.assertRegex(block, r'--hash=sha256:[0-9a-f]{64}')
            entries[req.name.lower().replace('_', '-')] = req
        declared = [Requirement(line) for line in (ROOT / 'docker/requirements.in').read_text().splitlines()
                    if line.strip() and not line.startswith('#')]
        for requirement in declared:
            self.assertEqual(entries[requirement.name].specifier, requirement.specifier)
        # FastMCP 4 no longer uses the legacy diskcache/keyring authentication graph.
        # Check its actual conditional SDK dependency rather than reinstating old packages.
        self.assertNotIn('diskcache', entries)
        self.assertIn('pywin32', entries)
        for python in ('3.12', '3.13'):
            for system in ('linux', 'darwin', 'win32'):
                env = {**default_environment(), 'python_version': python,
                       'python_full_version': python + '.0', 'implementation_version': python + '.0',
                       'sys_platform': system, 'os_name': 'nt' if system == 'win32' else 'posix',
                       'platform_system': {'linux': 'Linux', 'darwin': 'Darwin', 'win32': 'Windows'}[system],
                       'implementation_name': 'cpython', 'platform_python_implementation': 'CPython'}
                active = {name for name, req in entries.items() if req.marker is None or req.marker.evaluate(env)}
                self.assertEqual('pywin32' in active, system == 'win32')


if __name__ == '__main__':
    unittest.main()
