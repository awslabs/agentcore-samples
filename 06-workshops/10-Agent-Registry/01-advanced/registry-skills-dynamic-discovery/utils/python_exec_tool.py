"""Custom tools that execute Python code and shell commands non-interactively.

Replaces strands_tools.shell which requires interactive terminal approval
that fails in non-interactive environments (Jupyter notebooks, CI/CD, etc.).
"""

import os
import subprocess
import sys
import tempfile
import traceback

from strands import tool


@tool
def python_exec(code: str, working_dir: str = "") -> str:
    """Execute Python code and return the output.

    Use this tool to run Python scripts. The code runs in a separate Python
    process so all installed packages are available.

    Args:
        code: Python code to execute.
        working_dir: Optional directory to cd into before execution.

    Returns:
        Captured stdout/stderr output, or error traceback.
    """
    try:
        cwd = working_dir or None
        if working_dir:
            os.makedirs(working_dir, exist_ok=True)

        # Write code to a temp file and execute in a subprocess
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, dir=cwd) as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        try:
            result = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=120,
            )
            output = result.stdout + result.stderr
            return output.strip() or "Code executed successfully (no output)."
        finally:
            os.unlink(tmp_path)

    except subprocess.TimeoutExpired:
        return "Error: Code execution timed out after 120 seconds."
    except Exception:
        return f"Error:\n{traceback.format_exc()}"


@tool
def run_shell(command: str, working_dir: str = "") -> str:
    """Execute a shell command without interactive approval.

    Use this for non-Python commands (e.g. ls, pip install, etc.).

    Args:
        command: Shell command to execute.
        working_dir: Optional working directory.

    Returns:
        Command stdout/stderr output.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,  # nosec B602 - shell=True is intentional for this tool
            capture_output=True,
            text=True,
            cwd=working_dir or None,
            timeout=120,
        )
        output = result.stdout + result.stderr
        return output.strip() or "Command executed successfully (no output)."
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 120 seconds."
    except Exception:
        return f"Error:\n{traceback.format_exc()}"
