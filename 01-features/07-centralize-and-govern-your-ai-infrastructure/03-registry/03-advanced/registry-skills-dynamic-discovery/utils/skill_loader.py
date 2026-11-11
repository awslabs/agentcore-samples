"""
Utility to load an Agent Skill from a Registry search response.

Parses the registry record, downloads all skill files from the GitHub
repository reference, creates the local folder structure, and installs
any declared packages.

SECURITY WARNING
================
Registry record metadata is publisher-provided data that the registry stores
without interpretation. Per the AWS Shared Responsibility Model, consumers are
responsible for validating this metadata before acting on it, just as they
would validate input from any external data source.

This loader validates all metadata against allowlists before execution:
- Package identifiers and exact versions are checked against ALLOWED_PACKAGES
- Repository URLs are validated against ALLOWED_REPOSITORY_HOSTS and ALLOWED_REPOSITORIES

For production use, customize security_config.py with your organization's approved
packages and repositories, or integrate with AWS CodeArtifact.

See README.md "Security Considerations" for detailed guidance.

Usage:
    from utils.skill_loader import load_skill_from_registry

    skill_dir, skill_md = load_skill_from_registry(search_response, base_dir="./skills")
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

from utils.security_config import (
    SecurityValidationError,
    sanitize_path_component,
    validate_package,
    validate_repository_url,
    validate_url_scheme,
)

# GitHub API base
GITHUB_API = "https://api.github.com/repos"


def _parse_github_url(url):
    """Extract owner, repo, branch, and path from a GitHub tree URL.

    Example: https://github.com/anthropics/skills/tree/main/skills/pdf
    Returns: ("anthropics", "skills", "main", "skills/pdf")
    """
    parts = url.replace("https://github.com/", "").split("/")
    owner, repo = parts[0], parts[1]
    # parts[2] == "tree", parts[3] == branch
    branch = parts[3]
    path = "/".join(parts[4:])
    return owner, repo, branch, path


def _fetch_github_contents(owner, repo, path, branch="main"):
    """Fetch directory listing or file content from GitHub API.

    SECURITY: URL is constructed from validated repository components.
    The base URL (api.github.com) is hardcoded and trusted.
    """
    url = f"{GITHUB_API}/{owner}/{repo}/contents/{path}?ref={branch}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})

    # Validate URL scheme before making request
    validate_url_scheme(url)

    with urllib.request.urlopen(req) as resp:  # nosec B310 - URL scheme validated above
        return json.loads(resp.read().decode())


def _download_file(download_url, dest_path):
    """Download a single file from a raw GitHub URL.

    SECURITY: URL scheme is validated before download.
    Destination path should be within the skill directory.
    """
    # Validate URL scheme (HTTPS only)
    validate_url_scheme(download_url)

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    urllib.request.urlretrieve(download_url, dest_path)  # nosec B310 - URL scheme validated above


def _download_github_tree(owner, repo, branch, remote_path, local_dir, root_remote_path=None):
    """Recursively download all files from a GitHub directory.

    SECURITY: All file paths are sanitized to prevent path traversal.
    Download URLs from GitHub API are validated for HTTPS scheme.

    Args:
        owner: Repository owner (validated upstream)
        repo: Repository name (validated upstream)
        branch: Branch name
        remote_path: Path within repository
        local_dir: Local directory to download to
        root_remote_path: The top-level remote path used to compute relative paths.
                          Set automatically on the first call.
    """
    if root_remote_path is None:
        root_remote_path = remote_path

    contents = _fetch_github_contents(owner, repo, remote_path, branch)
    if not isinstance(contents, list):
        contents = [contents]

    for item in contents:
        # Compute path relative to the root remote path to preserve folder structure
        rel_path = item["path"][len(root_remote_path) :].lstrip("/")

        # SECURITY: Sanitize path components to prevent path traversal
        sanitized_parts = [sanitize_path_component(part) for part in rel_path.split("/") if part]
        local_path = os.path.join(local_dir, *sanitized_parts) if sanitized_parts else local_dir

        if item["type"] == "dir":
            _download_github_tree(owner, repo, branch, item["path"], local_dir, root_remote_path)
        else:
            _download_file(item["download_url"], local_path)
            print(f"  Downloaded: {rel_path}")


def _install_packages(packages):
    """Install packages declared in the skill definition.

    SECURITY: All packages are validated against the allowlist in security_config.py
    before installation. Unknown packages or versions will raise SecurityValidationError.

    For production deployments, consider using AWS CodeArtifact with upstream filtering
    instead of public PyPI/npm registries.
    """
    for pkg in packages:
        registry_type = pkg.get("registryType", "").lower()
        identifier = pkg["identifier"]
        version = pkg.get("version", "")

        # SECURITY: Validate package against allowlist before any installation
        # This prevents installation of arbitrary packages from registry metadata
        try:
            validate_package(identifier, version, registry_type)
        except SecurityValidationError as e:
            print(f"  ❌ SECURITY: {e}")
            raise

        pkg_spec = f"{identifier}=={version}" if version else identifier

        if registry_type == "pypi":
            print(f"  Installing (pip): {pkg_spec}")

            # The allowlist check above guarantees pkg_spec is an approved name and an
            # exact approved version (name==version).
            #   --only-binary=:all:    install pre-built wheels only; building from source
            #                          would run the package's setup code.
            #   sys.executable -m pip  install into the same environment this sample runs in.
            #
            # The packages' own dependencies are resolved by pip and are not covered by the
            # allowlist. For production, point pip at AWS CodeArtifact (--index-url) or use
            # hash-pinned requirements (--require-hashes). See README "Security Considerations".
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "--only-binary=:all:", pkg_spec],
                check=True,
            )

        elif registry_type == "npm":
            print(f"  Installing (npm): {pkg_spec}")
            # SECURITY: npm packages are validated against the same allowlist above.
            # For production, consider a private registry such as AWS CodeArtifact.
            subprocess.run(["npm", "install", pkg_spec], check=True)
        else:
            print(f"  Skipping unknown registry type: {registry_type} for {identifier}")


def load_skill_from_registry(search_response, record_index=0, base_dir="./skills"):
    """Parse a registry search response and set up the skill locally.

    SECURITY: This function validates all registry metadata before acting on it:
    - Repository URLs are validated against the allowlist
    - Package identifiers and exact versions are validated against the allowlist
    - File paths are sanitized to prevent path traversal

    Steps:
        1. Extract skillMd and skillDefinition from the response
        2. Validate repository URL against allowlist
        3. Download all files from the GitHub repository reference
        4. Validate and install declared packages
        5. Return the skill directory path and SKILL.md content

    Args:
        search_response: The full search_registry_records response dict.
        record_index: Which record to use if multiple results (default 0).
        base_dir: Parent directory where the skill folder will be created.

    Returns:
        (skill_dir, skill_md_content) tuple.

    Raises:
        SecurityValidationError: If any metadata fails security validation
    """
    record = search_response["registryRecords"][record_index]
    agent_skills = record["descriptors"]["agentSkillsDefinition"]

    # 1. Parse skill content
    skill_md_content = agent_skills["additionalData"]["skillMd"]["data"]
    skill_def = json.loads(agent_skills["data"])

    # Extract the skill name from SKILL.md frontmatter (the `name:` field)
    # This MUST match the directory name for the AgentSkills plugin
    skill_name = record["name"]
    for line in skill_md_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            skill_name = stripped[len("name:") :].strip()
            break

    # SECURITY: Sanitize skill name to prevent path traversal
    skill_name = sanitize_path_component(skill_name)

    print(f"Loading skill: {skill_name}")

    # 2. Create local skill directory (name matches SKILL.md frontmatter)
    skill_dir = os.path.join(base_dir, skill_name)
    os.makedirs(skill_dir, exist_ok=True)

    # 3. Write SKILL.md locally
    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    with open(skill_md_path, "w", encoding="utf-8") as f:
        f.write(skill_md_content)
    print("  Written: SKILL.md")

    # 4. Download remaining files from GitHub repo
    repo_info = skill_def.get("repository", {})
    repo_url = repo_info.get("url", "")

    if repo_url:
        # SECURITY: Validate repository URL against allowlist before connecting
        # This prevents downloading from arbitrary/malicious URLs in registry metadata
        try:
            validate_repository_url(repo_url)
        except SecurityValidationError as e:
            print(f"  ❌ SECURITY: {e}")
            raise

        owner, repo, branch, remote_path = _parse_github_url(repo_url)
        print(f"  Downloading from: {owner}/{repo}/{remote_path} (branch: {branch})")

        contents = _fetch_github_contents(owner, repo, remote_path, branch)
        for item in contents:
            # Skip SKILL.md since we already wrote it from registry data
            if item["name"].upper() == "SKILL.MD":
                continue

            if item["type"] == "dir":
                # Pass remote_path as root so rel paths are relative to skill root
                _download_github_tree(owner, repo, branch, item["path"], skill_dir, remote_path)
            else:
                # SECURITY: Sanitize filename
                safe_name = sanitize_path_component(item["name"])
                dest = os.path.join(skill_dir, safe_name)
                _download_file(item["download_url"], dest)
                print(f"  Downloaded: {item['name']}")

    # 5. Install packages
    # SECURITY: All packages are validated against allowlist before installation
    packages = skill_def.get("packages", [])
    if packages:
        print("  Installing packages...")
        _install_packages(packages)

    # 6. Print final structure
    print(f"\nSkill folder ready: {os.path.abspath(skill_dir)}")
    for root, dirs, files in os.walk(skill_dir):
        level = root.replace(skill_dir, "").count(os.sep)
        indent = "  " * level
        print(f"  {indent}{os.path.basename(root)}/")
        for f in files:
            print(f"  {indent}  {f}")

    return skill_dir, skill_md_content
