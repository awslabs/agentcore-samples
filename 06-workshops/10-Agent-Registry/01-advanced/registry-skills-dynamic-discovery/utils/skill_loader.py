"""Utility to load an Agent Skill from a Registry search response.

Parses the registry record, downloads all skill files from the GitHub
repository reference, creates the local folder structure, and installs
any declared packages.

Usage:
    from util.skill_loader import load_skill_from_registry

    skill_dir, skill_md = load_skill_from_registry(search_response, base_dir="./skills")
"""

import json
import os
import subprocess
import urllib.request
import urllib.error


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
    """Fetch directory listing or file content from GitHub API."""
    url = f"{GITHUB_API}/{owner}/{repo}/contents/{path}?ref={branch}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
    if not url.startswith("https://"):  # Validate URL scheme (bandit B310)
        raise ValueError(f"Only HTTPS URLs are allowed, got: {url}")
    with urllib.request.urlopen(req) as resp:  # nosec B310 - URL scheme validated above
        return json.loads(resp.read().decode())


def _download_file(download_url, dest_path):
    """Download a single file from a raw GitHub URL."""
    if not download_url.startswith("https://"):  # Validate URL scheme (bandit B310)
        raise ValueError(f"Only HTTPS URLs are allowed, got: {download_url}")
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    urllib.request.urlretrieve(download_url, dest_path)  # nosec B310 - URL scheme validated above


def _download_github_tree(owner, repo, branch, remote_path, local_dir, root_remote_path=None):
    """Recursively download all files from a GitHub directory.

    Args:
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
        local_path = os.path.join(local_dir, rel_path)

        if item["type"] == "dir":
            _download_github_tree(owner, repo, branch, item["path"], local_dir, root_remote_path)
        else:
            _download_file(item["download_url"], local_path)
            print(f"  Downloaded: {rel_path}")


def _install_packages(packages):
    """Install packages declared in the skill definition."""
    for pkg in packages:
        registry = pkg.get("registryType", "")
        identifier = pkg["identifier"]
        version = pkg.get("version", "")
        pkg_spec = f"{identifier}=={version}" if version else identifier

        if registry == "pypi":
            print(f"  Installing (pip): {pkg_spec}")
            subprocess.run(["pip", "install", "-q", pkg_spec], check=True)
        elif registry == "npm":
            print(f"  Installing (npm): {pkg_spec}")
            subprocess.run(["npm", "install", pkg_spec], check=True)
        else:
            print(f"  Skipping unknown registry type: {registry} for {identifier}")


def load_skill_from_registry(search_response, record_index=0, base_dir="./skills"):
    """Parse a registry search response and set up the skill locally.

    Steps:
        1. Extract skillMd and skillDefinition from the response
        2. Download all files from the GitHub repository reference
        3. Install declared packages
        4. Return the skill directory path and SKILL.md content

    Args:
        search_response: The full search_registry_records response dict.
        record_index: Which record to use if multiple results (default 0).
        base_dir: Parent directory where the skill folder will be created.

    Returns:
        (skill_dir, skill_md_content) tuple.
    """
    record = search_response["registryRecords"][record_index]
    agent_skills = record["descriptors"]["agentSkills"]

    # 1. Parse skill content
    skill_md_content = agent_skills["skillMd"]["inlineContent"]
    skill_def = json.loads(agent_skills["skillDefinition"]["inlineContent"])

    # Extract the skill name from SKILL.md frontmatter (the `name:` field)
    # This MUST match the directory name for the AgentSkills plugin
    skill_name = record["name"]
    for line in skill_md_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            skill_name = stripped[len("name:") :].strip()
            break

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
        owner, repo, branch, remote_path = _parse_github_url(repo_url)
        print(f"  Downloading from: {owner}/{repo}/{remote_path} (branch: {branch})")

        contents = _fetch_github_contents(owner, repo, remote_path, branch)
        for item in contents:
            # Skip SKILL.md since we already wrote it from inlineContent
            if item["name"].upper() == "SKILL.MD":
                continue

            if item["type"] == "dir":
                # Pass remote_path as root so rel paths are relative to skill root
                _download_github_tree(owner, repo, branch, item["path"], skill_dir, remote_path)
            else:
                dest = os.path.join(skill_dir, item["name"])
                _download_file(item["download_url"], dest)
                print(f"  Downloaded: {item['name']}")

    # 5. Install packages
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
