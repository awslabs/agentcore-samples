"""Service-safe Memory IDs without putting raw claimant email in resource paths.

SHA-256 of the exact UTF-8 identity keeps repeat claimants stable without merging
case-distinct identities. This is an identifier, not encryption/anonymization.
The original email remains in the request for notification/claim processing.

Botocore 1.44.10 bounds actorId at 255 and sessionId at 100 characters. Our
lowercase hex + ASCII prefixes also fit its actor/session/namespace patterns;
regressions explicitly compare those pinned patterns (ParamValidator does not).
"""

import hashlib
import uuid


def claimant_memory_ids(identity: str) -> tuple[str, str]:
    """Return a stable 73-character actor key and a fresh 38-character session ID."""
    if not isinstance(identity, str) or not identity:
        raise ValueError("Memory claimant identity must be a nonempty string")
    actor_id = "claimant-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    session_id = "claim-" + uuid.uuid4().hex
    return actor_id, session_id
