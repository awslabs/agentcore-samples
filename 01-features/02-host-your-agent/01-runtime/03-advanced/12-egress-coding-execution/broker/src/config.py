import fnmatch
import os
from typing import Optional


class BrokerConfig:
    def __init__(self):
        self._allowed_ping_domains = self._parse_env("ALLOWED_PING_DOMAINS", "")
        self._allowed_domains = self._parse_env("ALLOWED_DOMAINS", "")

    def _parse_env(self, key: str, default: str) -> list:
        raw = os.environ.get(key, default)
        return [p.strip() for p in raw.split(",") if p.strip()]

    @property
    def allowed_ping_domains(self) -> list:
        return list(self._allowed_ping_domains)

    @property
    def allowed_domains(self) -> list:
        return list(self._allowed_domains)

    def update(
        self,
        allowed_ping_domains: Optional[list] = None,
        allowed_domains: Optional[list] = None,
    ) -> list:
        updated = []
        if allowed_ping_domains is not None:
            self._allowed_ping_domains = list(allowed_ping_domains)
            updated.append("allowed_ping_domains")
        if allowed_domains is not None:
            self._allowed_domains = list(allowed_domains)
            updated.append("allowed_domains")
        return updated

    def is_ping_domain_allowed(self, domain: str) -> bool:
        for pattern in self.allowed_ping_domains:
            if fnmatch.fnmatch(domain, pattern):
                return True
        return False


broker_config = BrokerConfig()
