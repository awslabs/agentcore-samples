"""Import shared utilities from root scripts directory"""

import sys
from pathlib import Path

# Add root scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from shared_utils import (
    get_ssm_parameter,
    get_aws_info,
    invoke_endpoint,
    get_m2m_token_for_agent,
)

__all__ = [
    "get_ssm_parameter",
    "get_aws_info",
    "invoke_endpoint",
    "get_m2m_token_for_agent",
]
