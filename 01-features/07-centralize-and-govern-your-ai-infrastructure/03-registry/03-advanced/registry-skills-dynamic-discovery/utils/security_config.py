"""
Security Configuration for Skill Loader

This module provides centralized security configuration for validating registry
record metadata before execution.

IMPORTANT: Registry record metadata is publisher-provided data that the registry
stores without interpretation. Per the AWS Shared Responsibility Model, consumers
are responsible for validating this metadata before acting on it, just as they
would validate input from any external data source.

This configuration demonstrates safe consumption patterns:
- Package allowlist with exact, pinned versions
- Repository URL allowlist with approved hosts and paths
- Validation functions that reject unknown/untrusted sources

For production deployments, consider:
- AWS CodeArtifact for package management with upstream filtering
- Hash-pinned requirements (pip install --require-hashes) to verify package contents
- Amazon Bedrock Guardrails for content validation
- EventBridge-triggered automated scanning of new registry records

References:
- AWS Shared Responsibility Model: https://aws.amazon.com/compliance/shared-responsibility-model/
- Amazon Bedrock Guardrails: https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html
- AWS Well-Architected Security Pillar: https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/
"""

from urllib.parse import urlparse


class SecurityValidationError(Exception):
    """Raised when security validation fails for registry metadata."""


# =============================================================================
# PACKAGE ALLOWLIST CONFIGURATION
# =============================================================================
# Define approved packages and the exact versions that may be installed.
# A package that is not listed, or a version that is not listed, is rejected.
#
# NOTE: This list covers the packages named in the skill definition. Their own
# dependencies are resolved by pip and are not part of this list.
#
# For production use, consider:
# - Maintaining this list in a separate configuration file or AWS Systems Manager Parameter Store
# - Using AWS CodeArtifact with upstream filtering instead of public PyPI
# - Using hash-pinned requirements (pip install --require-hashes) for the full dependency tree

ALLOWED_PACKAGES = {
    "pypi": {
        "pypdf": ["5.1.0"],
        "reportlab": ["4.4.0"],
    },
    "npm": {
        # Add npm packages here following the same pattern
        # "package-name": ["1.2.3"],
    },
}


# =============================================================================
# REPOSITORY URL ALLOWLIST CONFIGURATION
# =============================================================================
# Define approved repository hosts and specific repository paths.
# URLs not matching these patterns will be rejected.

ALLOWED_REPOSITORY_HOSTS = [
    "github.com",
    # Add additional approved hosts:
    # "github.your-company.com",
    # "gitlab.com",
]

ALLOWED_REPOSITORIES = [
    "anthropics/skills",
    # Add additional approved repositories:
    # "your-org/approved-skills",
]

# URL schemes that are permitted (HTTPS only for security)
ALLOWED_URL_SCHEMES = ["https"]


# =============================================================================
# VALIDATION FUNCTIONS
# =============================================================================


def validate_package(identifier: str, version: str, registry_type: str = "pypi") -> None:
    """
    Validate a package against the allowlist before installation.

    SECURITY: Registry record metadata (packages[].identifier, packages[].version)
    is publisher-provided data. This function ensures only pre-approved packages
    with known versions can be installed. A missing version is rejected, so every
    install is pinned to an exact approved version.

    Args:
        identifier: Package name (e.g., "pypdf")
        version: Package version (e.g., "5.1.0")
        registry_type: Package registry type ("pypi", "npm")

    Raises:
        SecurityValidationError: If package or version is not in the allowlist
    """
    registry_packages = ALLOWED_PACKAGES.get(registry_type, {})

    if identifier not in registry_packages:
        raise SecurityValidationError(
            f"Package '{identifier}' not in allowlist for registry '{registry_type}'. "
            f"Approved packages: {list(registry_packages.keys())}. "
            f"Add to security_config.py if approved for use."
        )

    allowed_versions = registry_packages[identifier]
    if version not in allowed_versions:
        raise SecurityValidationError(
            f"Version '{version}' of '{identifier}' not in allowlist. "
            f"Approved versions: {allowed_versions}. "
            f"Add to security_config.py if approved for use."
        )


def validate_repository_url(url: str) -> None:
    """
    Validate a repository URL against the allowlist before connecting.

    SECURITY: Registry record metadata (repository.url) is publisher-provided data.
    This function ensures only pre-approved repository hosts and paths are accessed.

    Args:
        url: Repository URL to validate

    Raises:
        SecurityValidationError: If URL scheme, host, or path is not allowed
    """
    parsed = urlparse(url)

    # Validate URL scheme (HTTPS only)
    if parsed.scheme not in ALLOWED_URL_SCHEMES:
        raise SecurityValidationError(
            f"URL scheme '{parsed.scheme}' not allowed. Only {ALLOWED_URL_SCHEMES} are permitted."
        )

    # Validate host
    if parsed.netloc not in ALLOWED_REPOSITORY_HOSTS:
        raise SecurityValidationError(
            f"Repository host '{parsed.netloc}' not in allowlist. "
            f"Approved hosts: {ALLOWED_REPOSITORY_HOSTS}. "
            f"Add to security_config.py if approved for use."
        )

    # Validate repository path (e.g., "owner/repo" from github.com/owner/repo/...)
    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) >= 2:
        repo_path = f"{path_parts[0]}/{path_parts[1]}"
        if repo_path not in ALLOWED_REPOSITORIES:
            raise SecurityValidationError(
                f"Repository '{repo_path}' not in allowlist. "
                f"Approved repositories: {ALLOWED_REPOSITORIES}. "
                f"Add to security_config.py if approved for use."
            )


def validate_url_scheme(url: str) -> None:
    """
    Validate that a URL uses an allowed scheme (HTTPS).

    Args:
        url: URL to validate

    Raises:
        SecurityValidationError: If URL scheme is not allowed
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_URL_SCHEMES:
        raise SecurityValidationError(
            f"URL scheme '{parsed.scheme}' not allowed for URL: {url}. Only {ALLOWED_URL_SCHEMES} are permitted."
        )


def sanitize_path_component(component: str) -> str:
    """
    Sanitize a path component to prevent path traversal attacks.

    Args:
        component: Path component to sanitize

    Returns:
        Sanitized path component

    Raises:
        SecurityValidationError: If path component contains dangerous patterns
    """
    # Check for path traversal attempts
    if ".." in component or component.startswith("/"):
        raise SecurityValidationError(f"Invalid path component (potential path traversal): {component}")

    # Reject null bytes
    if "\x00" in component:
        raise SecurityValidationError(f"Invalid path component (contains null byte): {component}")

    return component
