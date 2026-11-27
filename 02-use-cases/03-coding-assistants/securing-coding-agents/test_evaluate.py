import unittest

from cedarpy import PolicySet
from evaluate import GATEWAY, decide, load_policies


class PolicyDecisions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policies = load_policies()

    def test_workspace(self):
        for operation in ["read_file", "write_file", "list_directory"]:
            with self.subTest(operation=operation):
                action = "FileSystemTarget___" + operation
                self.assertEqual(decide(self.policies, action, {"path": "/workspace/src/demo"}), "ALLOW")
                self.assertEqual(decide(self.policies, action, {"path": "/other/demo"}), "DEFAULT DENY")
                self.assertEqual(decide(self.policies, action, {}), "DEFAULT DENY")

    def test_commands(self):
        for cmd in ["npm test", "npm run build", "python -m pytest"]:
            self.assertEqual(decide(self.policies, "ShellTarget___execute", {"command": cmd}), "ALLOW")
        self.assertEqual(decide(self.policies, "ShellTarget___execute", {"command": "other"}), "DEFAULT DENY")
        self.assertEqual(decide(self.policies, "ShellTarget___execute", {"command": "sudo"}), "EXPLICIT DENY")

    def test_tool_restrictions(self):
        for action in [
            "ApiTarget___invoke",
            "CodeTarget___python_repl",
            "CodeTarget___execute_code",
            "RetrieveTarget___retrieve",
            "HttpTarget___http_request",
        ]:
            with self.subTest(action=action):
                self.assertEqual(decide(self.policies, action, {}), "EXPLICIT DENY")

    def test_resource_scope(self):
        self.assertEqual(
            decide(self.policies, "ShellTarget___execute", {"command": "npm test"}, GATEWAY + "-other"), "DEFAULT DENY"
        )

    def test_forbid_overrides_permit(self):
        combined = PolicySet.from_str(
            str(self.policies)
            + '\npermit(principal, action == AgentCore::Action::"HttpTarget___http_request", resource);'
        )
        self.assertEqual(decide(combined, "HttpTarget___http_request", {}), "EXPLICIT DENY")
