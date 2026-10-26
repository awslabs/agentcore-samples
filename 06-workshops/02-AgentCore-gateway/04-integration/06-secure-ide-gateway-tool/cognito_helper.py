"""Helper functions for provisioning the Cognito user used by the IDE OAuth flow.

The Cognito password is randomly generated on each run and persisted to a local
`.env` file (never committed - see .gitignore) so it can be reloaded with
`load_dotenv` instead of being hard-coded in the notebook.
"""

import os
import secrets
import string
from pathlib import Path

import boto3
from dotenv import load_dotenv

DEFAULT_ENV_PATH = Path(".env")


def generate_cognito_password(length: int = 20) -> str:
    """Generate a random password meeting Cognito's default password policy."""
    special_chars = "!@#$%^&*()-_=+"
    alphabet = string.ascii_letters + string.digits + special_chars
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(special_chars),
    ]
    remaining = [secrets.choice(alphabet) for _ in range(length - len(required))]
    password_chars = required + remaining
    secrets.SystemRandom().shuffle(password_chars)
    return "".join(password_chars)


def _append_password(password: str, env_path: Path) -> None:
    """Append COGNITO_PASSWORD to the .env file without touching existing content."""
    prefix = "\n" if env_path.exists() and env_path.read_text() else ""
    with env_path.open("a") as f:
        f.write(f"{prefix}COGNITO_PASSWORD={password}\n")


def create_cognito_user(
    username: str,
    user_pool_id: str,
    env_path: Path = DEFAULT_ENV_PATH,
) -> str:
    """Create (or update) the Cognito user, reusing the password already stored in `.env`.

    If `.env` doesn't exist yet or has no COGNITO_PASSWORD entry, a new random
    password is generated and appended to it (existing content is left untouched).
    Otherwise the existing password is reused so re-running this doesn't rotate
    the password (and doesn't overwrite unrelated .env entries).

    Returns:
        The Cognito password that was set for the user.
    """
    load_dotenv(dotenv_path=env_path, override=True)
    password = os.environ.get("COGNITO_PASSWORD")

    if not password:
        password = generate_cognito_password()
        _append_password(password, env_path)
        load_dotenv(dotenv_path=env_path, override=True)

    cognito = boto3.client("cognito-idp")
    try:
        cognito.admin_create_user(
            UserPoolId=user_pool_id,
            Username=username,
            TemporaryPassword=password,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": username},
                {"Name": "email_verified", "Value": "true"},
            ],
        )
        cognito.admin_set_user_password(
            UserPoolId=user_pool_id, Username=username, Password=password, Permanent=True
        )
        print(f"✓ User created: {username}")
    except cognito.exceptions.UsernameExistsException:
        cognito.admin_set_user_password(
            UserPoolId=user_pool_id, Username=username, Password=password, Permanent=True
        )
        print(f"✓ User exists: {username} (password updated)")

    return password
