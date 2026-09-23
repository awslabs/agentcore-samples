"""Unit tests for the broker allowlist policy (broker/src/config.py)."""

from src.config import BrokerConfig


def test_exact_domain_match_allowed():
    cfg = BrokerConfig()
    cfg.update(allowed_ping_domains=["google.com", "amazon.com"])
    assert cfg.is_ping_domain_allowed("google.com") is True
    assert cfg.is_ping_domain_allowed("amazon.com") is True


def test_domain_not_in_allowlist_is_denied():
    cfg = BrokerConfig()
    cfg.update(allowed_ping_domains=["google.com"])
    assert cfg.is_ping_domain_allowed("example.com") is False


def test_glob_wildcard_matching():
    """fnmatch glob patterns (e.g. *.amazon.com) are honored."""
    cfg = BrokerConfig()
    cfg.update(allowed_ping_domains=["*.amazon.com"])
    assert cfg.is_ping_domain_allowed("aws.amazon.com") is True
    assert cfg.is_ping_domain_allowed("docs.amazon.com") is True
    assert cfg.is_ping_domain_allowed("amazon.com") is False  # no subdomain


def test_empty_allowlist_denies_everything():
    cfg = BrokerConfig()
    assert cfg.is_ping_domain_allowed("google.com") is False


def test_update_reports_which_lists_changed():
    cfg = BrokerConfig()
    updated = cfg.update(allowed_ping_domains=["a.com"], allowed_domains=["b.com"])
    assert set(updated) == {"allowed_ping_domains", "allowed_domains"}

    # Only the ping list this time.
    updated = cfg.update(allowed_ping_domains=["c.com"])
    assert updated == ["allowed_ping_domains"]


def test_allowlist_getters_return_copies():
    """Mutating a returned list must not affect internal state."""
    cfg = BrokerConfig()
    cfg.update(allowed_ping_domains=["google.com"])
    returned = cfg.allowed_ping_domains
    returned.append("evil.com")
    assert "evil.com" not in cfg.allowed_ping_domains
