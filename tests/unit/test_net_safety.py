"""Unit tests for SSRF protection — net_safety.is_private_url().

Tests that private, loopback, link-local, and reserved IPs are blocked
when used as URL targets, preventing server-side request forgery.

Created session 72.
"""

import pytest
from unittest.mock import patch

from src.utils.net_safety import is_private_url


class TestIsPrivateUrl:
    """Tests for is_private_url() — SSRF guard."""

    # ── Obvious private/loopback addresses ──────────────────────────

    def test_localhost_blocked(self):
        """localhost is always blocked."""
        assert is_private_url("http://localhost/api") is True
        assert is_private_url("http://localhost:8080/admin") is True

    def test_loopback_ipv4_blocked(self):
        """127.x.x.x addresses are blocked."""
        assert is_private_url("http://127.0.0.1/") is True
        assert is_private_url("http://127.0.0.1:3000/api") is True
        assert is_private_url("http://127.1.2.3/") is True

    def test_private_class_a_blocked(self):
        """10.x.x.x private addresses are blocked."""
        assert is_private_url("http://10.0.0.1/") is True
        assert is_private_url("http://10.255.255.255/") is True

    def test_private_class_b_blocked(self):
        """172.16-31.x.x private addresses are blocked."""
        assert is_private_url("http://172.16.0.1/") is True
        assert is_private_url("http://172.31.255.255/") is True

    def test_private_class_c_blocked(self):
        """192.168.x.x private addresses are blocked."""
        assert is_private_url("http://192.168.0.1/") is True
        assert is_private_url("http://192.168.1.100:8080/") is True

    def test_link_local_blocked(self):
        """169.254.x.x link-local addresses are blocked (cloud metadata)."""
        assert is_private_url("http://169.254.169.254/latest/meta-data/") is True

    # ── Public addresses ────────────────────────────────────────────

    def test_public_ip_allowed(self):
        """Public IP addresses are allowed."""
        assert is_private_url("http://8.8.8.8/") is False
        assert is_private_url("https://1.1.1.1/") is False

    def test_public_domain_allowed(self):
        """Public domains (google.com, etc.) are allowed."""
        # This resolves via DNS — should resolve to a public IP
        assert is_private_url("https://www.google.com/") is False

    # ── Edge cases ──────────────────────────────────────────────────

    def test_empty_host_blocked(self):
        """Empty hostname is blocked."""
        assert is_private_url("http:///path") is True

    def test_dns_failure_allows(self):
        """DNS resolution failure allows the URL (fetch fails naturally)."""
        # A non-existent domain — DNS will fail
        result = is_private_url("http://this-domain-definitely-does-not-exist-xyz123.invalid/")
        assert result is False  # Allowed — DNS failure path

    def test_ipv6_loopback_blocked(self):
        """IPv6 loopback (::1) is blocked."""
        assert is_private_url("http://[::1]/") is True

    def test_zero_address_blocked(self):
        """0.0.0.0 is blocked (reserved/unspecified)."""
        assert is_private_url("http://0.0.0.0/") is True

    def test_url_with_auth_parsed_correctly(self):
        """URLs with user:pass@ are parsed correctly for hostname."""
        assert is_private_url("http://user:pass@127.0.0.1/admin") is True
        # Public host with auth should still be allowed
        assert is_private_url("http://user:pass@8.8.8.8/") is False

    def test_url_with_port_parsed_correctly(self):
        """Port numbers don't affect hostname parsing."""
        assert is_private_url("http://192.168.1.1:9200/") is True
        assert is_private_url("http://10.0.0.5:443/api") is True

    @patch("src.utils.net_safety.socket.getaddrinfo")
    def test_dns_resolving_to_private_blocked(self, mock_dns):
        """A public-looking domain that DNS-resolves to a private IP is blocked."""
        # Simulate a domain resolving to 192.168.1.1
        mock_dns.return_value = [
            (2, 1, 0, "", ("192.168.1.1", 80)),
        ]
        assert is_private_url("http://evil-rebind.example.com/") is True

    @patch("src.utils.net_safety.socket.getaddrinfo")
    def test_dns_resolving_to_public_allowed(self, mock_dns):
        """A domain resolving to a public IP is allowed."""
        mock_dns.return_value = [
            (2, 1, 0, "", ("93.184.216.34", 80)),
        ]
        assert is_private_url("http://example.com/") is False
