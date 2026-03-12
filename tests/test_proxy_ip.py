"""Tests for trusted proxy IP resolution — rate limiter bypass prevention.

Grok Elder Council finding (HIGH): request.client.host behind reverse proxy
returns proxy IP, not real client. Rate limiter bypassed via X-Forwarded-For
spoofing or IPv6 ::1.
"""
import pytest
from unittest.mock import MagicMock
from backend.main import _get_client_ip


class TestGetClientIp:
    """Client IP resolution with proxy awareness."""

    def test_direct_connection_uses_client_host(self):
        """Without trusted proxies, use request.client.host as-is."""
        request = MagicMock()
        request.client.host = "192.168.1.50"
        request.headers = {}
        assert _get_client_ip(request) == "192.168.1.50", \
            "Direct connection should use request.client.host"

    def test_forwarded_for_ignored_without_trust(self):
        """X-Forwarded-For is ignored when no proxies are trusted."""
        request = MagicMock()
        request.client.host = "172.18.0.2"
        request.headers = {"x-forwarded-for": "1.2.3.4, 172.18.0.2"}
        assert _get_client_ip(request) == "172.18.0.2", \
            "Without trusted proxies, X-Forwarded-For should be ignored"

    def test_forwarded_for_with_trusted_proxy(self):
        """With trusted proxy, extract real client from X-Forwarded-For."""
        request = MagicMock()
        request.client.host = "172.18.0.2"
        request.headers = {"x-forwarded-for": "203.0.113.50, 172.18.0.2"}
        assert _get_client_ip(request, trusted_proxies={"172.18.0.2"}) == "203.0.113.50", \
            "Should extract real client IP from X-Forwarded-For when proxy is trusted"

    def test_chained_proxies(self):
        """Multiple proxies — use rightmost untrusted IP."""
        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers = {"x-forwarded-for": "203.0.113.50, 10.0.0.2, 10.0.0.1"}
        trusted = {"10.0.0.1", "10.0.0.2"}
        assert _get_client_ip(request, trusted_proxies=trusted) == "203.0.113.50", \
            "Should walk X-Forwarded-For right-to-left, skip trusted proxies"

    def test_all_trusted_falls_back_to_leftmost(self):
        """If all IPs in chain are trusted, use the leftmost (origin)."""
        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers = {"x-forwarded-for": "10.0.0.3, 10.0.0.2"}
        trusted = {"10.0.0.1", "10.0.0.2", "10.0.0.3"}
        assert _get_client_ip(request, trusted_proxies=trusted) == "10.0.0.3", \
            "All trusted IPs should fall back to leftmost (origin)"

    def test_ipv6_localhost_normalised(self):
        """IPv6 ::1 should be treated as 127.0.0.1."""
        request = MagicMock()
        request.client.host = "::1"
        request.headers = {}
        assert _get_client_ip(request) == "127.0.0.1", \
            "IPv6 ::1 should be normalised to 127.0.0.1"

    def test_empty_forwarded_for_uses_client_host(self):
        """Empty X-Forwarded-For header falls back to client.host."""
        request = MagicMock()
        request.client.host = "192.168.1.50"
        request.headers = {"x-forwarded-for": ""}
        assert _get_client_ip(request, trusted_proxies={"172.18.0.2"}) == "192.168.1.50", \
            "Empty X-Forwarded-For should fall back to client.host"

    def test_no_client_returns_unknown(self):
        """Missing client info returns 'unknown'."""
        request = MagicMock()
        request.client = None
        request.headers = {}
        assert _get_client_ip(request) == "unknown", \
            "Missing client info should return 'unknown'"
