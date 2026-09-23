#!/usr/bin/env python3
"""
Patch browser_use to pass AgentCore authentication headers via CDP.

browser_use's BrowserProfile/Browser class does not natively forward
custom HTTP headers when connecting via CDP. This one-time patch edits
browser_use/browser/session.py to:
  1. Accept headers from BrowserProfile and store them.
  2. Pass those headers to CDPClient so AgentCore's SigV4 auth works.

Run once after installing browser-use:
    python patch_browser_use.py

A backup of the original file is saved as session.py.backup.
"""

import os
import shutil
import sys
from pathlib import Path


def find_browser_use_path() -> str | None:
    """Auto-detect the browser_use session.py path."""
    try:
        import browser_use  # noqa: F401

        session_file = Path(browser_use.__file__).parent / "browser" / "session.py"
        return str(session_file)
    except ImportError:
        print("ERROR: browser_use not installed. Run: pip install browser-use")
        return None


def patch_browser_use() -> bool:
    file_path = find_browser_use_path()
    if not file_path:
        return False
    if not os.path.exists(file_path):
        print(f"ERROR: File not found: {file_path}")
        return False

    print(f"Found browser_use at: {file_path}")

    with open(file_path, "r") as f:
        content = f.read()

    # Newer versions of browser_use (≥0.2.x) already read headers from
    # BrowserProfile and pass them to CDPClient via additional_headers.
    # Check whether this version is already patched or natively supports headers.
    native_support = (
        "getattr(self.browser_profile, 'headers'" in content
        or "additional_headers=headers" in content
        or "additional_headers=self.browser_profile.headers" in content
    )
    if native_support:
        print("This browser_use version already forwards BrowserProfile headers to CDPClient.")
        print("No patch needed — BrowserProfile(headers=...) works out of the box.")
        return True

    # Older versions need manual patching — create a backup first
    backup_path = file_path + ".backup"
    if not os.path.exists(backup_path):
        shutil.copy2(file_path, backup_path)
        print(f"Backup created: {backup_path}")
    else:
        print(f"Backup already exists: {backup_path}")

    patched = False

    # Patch 1: store headers from BrowserProfile into profile_kwargs
    old1 = "if not cdp_url:\n\t\t\tprofile_kwargs['is_local'] = True"
    new1 = (
        "if not cdp_url:\n\t\t\tprofile_kwargs['is_local'] = True\n\n"
        "\t\tif headers:\n\t\t\tprofile_kwargs['headers'] = headers"
    )
    if old1 in content and "profile_kwargs['headers'] = headers" not in content:
        content = content.replace(old1, new1)
        print("Patch 1 applied: headers stored in profile_kwargs")
        patched = True
    elif "profile_kwargs['headers'] = headers" in content:
        print("Patch 1 already applied")

    # Patch 2: forward headers to CDPClient
    old2 = "self._cdp_client_root = CDPClient(self.cdp_url)"
    new2 = "self._cdp_client_root = CDPClient(self.cdp_url, additional_headers=self.browser_profile.headers)"
    if old2 in content:
        content = content.replace(old2, new2)
        print("Patch 2 applied: headers forwarded to CDPClient")
        patched = True
    elif "additional_headers=self.browser_profile.headers" in content:
        print("Patch 2 already applied")

    if not patched:
        print("WARNING: No patch patterns matched. Inspect session.py manually.")
        print("The auth headers may not be forwarded to CDPClient in this version.")

    with open(file_path, "w") as f:
        f.write(content)

    print("Patching complete!")
    return True


if __name__ == "__main__":
    success = patch_browser_use()
    sys.exit(0 if success else 1)
