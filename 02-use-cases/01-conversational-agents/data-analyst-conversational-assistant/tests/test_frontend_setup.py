"""Exercise the config generator with fake aws/pnpm executables, never real AWS."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = "amplify-data-analyst-conversational-assistant-agentcore-strands"


class FrontendSetupTests(unittest.TestCase):
    def run_setup(self, missing_runtime=False):
        with tempfile.TemporaryDirectory(prefix="data-analyst-setup-") as directory:
            root = Path(directory)
            (root / FRONTEND).mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            shutil.copyfile(ROOT / "setup-frontend.sh", root / "setup-frontend.sh")
            aws = bin_dir / "aws"
            aws.write_text("""#!/bin/sh
if [ "$1" != cloudformation ] || [ "$2" != describe-stacks ]; then
  echo 'Unexpected AWS command' >&2
  exit 91
fi
case "$*" in
  *AgentRuntimeArn*)
    if [ "$MISSING_RUNTIME" = 1 ]; then echo None
    else echo arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/test-runtime; fi ;;
  *QuestionAnswersTableName*) echo test-query-results ;;
  *MemoryId*) echo test-memory ;;
  *) echo 'Unexpected output query' >&2; exit 92 ;;
esac
""")
            pnpm = bin_dir / "pnpm"
            pnpm.write_text("""#!/bin/sh
[ "$*" = install ] || exit 93
printf 'stubbed install; no packages downloaded\\n'
""")
            aws.chmod(0o755)
            pnpm.chmod(0o755)
            result = subprocess.run(
                ["/bin/bash", "setup-frontend.sh", "--region", "us-east-1", "--stack-name", "TestStack"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env={
                    "PATH": str(bin_dir) + ":/usr/bin:/bin",
                    "HOME": str(root),
                    "TMPDIR": str(root),
                    "MISSING_RUNTIME": "1" if missing_runtime else "0",
                    "AWS_EC2_METADATA_DISABLED": "true",
                    "AWS_ACCESS_KEY_ID": "testing",
                    "AWS_SECRET_ACCESS_KEY": "testing",
                    "AWS_CONFIG_FILE": str(root / "nonexistent-config"),
                    "AWS_SHARED_CREDENTIALS_FILE": str(root / "nonexistent-credentials"),
                },
            )
            env_file = root / FRONTEND / ".env.local"
            contents = env_file.read_text() if env_file.exists() else None
            return result, contents

    def test_preserves_cdk_outputs_and_frontend_environment(self):
        result, contents = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("stubbed install", result.stdout)
        self.assertIn(
            "AGENT_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/test-runtime", contents
        )
        self.assertIn("QUESTION_ANSWERS_TABLE_NAME=test-query-results", contents)
        self.assertIn("MEMORY_ID=test-memory", contents)
        self.assertIn("AGENT_ENDPOINT_NAME=DEFAULT", contents)
        self.assertIn("MODEL_ID_FOR_CHART=us.anthropic.claude-haiku-4-5-20251001-v1:0", contents)

    def test_missing_runtime_does_not_generate_environment_or_install(self):
        result, contents = self.run_setup(missing_runtime=True)
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(contents)
        self.assertNotIn("stubbed install", result.stdout)


if __name__ == "__main__":
    unittest.main()
