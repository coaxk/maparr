"""Tests for DOCKER_HOST validation — SSRF prevention.

Grok Elder Council finding (HIGH): resolver.py blindly honours any DOCKER_HOST URI.
A malicious/misconfigured env can point subprocess calls at internal services.
Guard: only allow unix://, tcp://127.*/localhost/socket-proxy patterns.
"""
import pytest
from backend.resolver import _validate_docker_host


class TestDockerHostAllowlist:
    """DOCKER_HOST environment variable validation."""

    def test_empty_is_allowed(self):
        """Empty/None DOCKER_HOST means use default socket."""
        assert _validate_docker_host(None) is None, "None DOCKER_HOST should return None (use default socket)"
        assert _validate_docker_host("") is None, "Empty DOCKER_HOST should return None (use default socket)"

    def test_unix_socket_allowed(self):
        """Standard unix socket paths are always allowed."""
        assert _validate_docker_host("unix:///var/run/docker.sock") == "unix:///var/run/docker.sock", "Standard docker socket path should be allowed"
        assert _validate_docker_host("unix:///run/docker.sock") == "unix:///run/docker.sock", "Alternate docker socket path should be allowed"

    def test_tcp_loopback_allowed(self):
        """TCP to localhost/127.x is allowed (local socket proxy)."""
        assert _validate_docker_host("tcp://127.0.0.1:2375") == "tcp://127.0.0.1:2375", "Loopback 127.0.0.1 should be allowed for local socket proxy"
        assert _validate_docker_host("tcp://127.0.0.1:2376") == "tcp://127.0.0.1:2376", "Loopback 127.0.0.1 on TLS port should be allowed"
        assert _validate_docker_host("tcp://localhost:2375") == "tcp://localhost:2375", "localhost with port should be allowed for local socket proxy"

    def test_tcp_socket_proxy_allowed(self):
        """Common socket proxy container names are allowed."""
        assert _validate_docker_host("tcp://socket-proxy:2375") == "tcp://socket-proxy:2375", "socket-proxy container name should be allowed"

    def test_tcp_dotlocal_allowed(self):
        """*.local hostnames are allowed (mDNS/local network)."""
        assert _validate_docker_host("tcp://docker.local:2375") == "tcp://docker.local:2375", "mDNS .local hostname should be allowed"

    def test_arbitrary_tcp_denied(self):
        """Arbitrary TCP hosts are SSRF vectors — must be rejected."""
        assert _validate_docker_host("tcp://192.168.1.100:2375") is None, "Private LAN IP should be denied as SSRF vector"
        assert _validate_docker_host("tcp://redis:6379") is None, "Arbitrary TCP host should be denied as SSRF vector"
        assert _validate_docker_host("tcp://internal-service:8080") is None, "Arbitrary service name should be denied as SSRF vector"
        assert _validate_docker_host("tcp://10.0.0.1:2375") is None, "10.x.x.x range should be denied as SSRF vector"

    def test_non_docker_schemes_denied(self):
        """Non-docker URI schemes must be rejected."""
        assert _validate_docker_host("http://evil.com") is None, "http:// scheme should be denied (not a Docker protocol)"
        assert _validate_docker_host("ssh://root@host") is None, "ssh:// scheme should be denied (not a Docker protocol)"
        assert _validate_docker_host("ftp://files.local") is None, "ftp:// scheme should be denied (not a Docker protocol)"

    def test_credential_uris_denied(self):
        """URIs with embedded credentials must be rejected."""
        assert _validate_docker_host("tcp://user:pass@host:2375") is None, "URI with embedded credentials must be denied"

    def test_denied_value_logs_warning(self, caplog):
        """Denied DOCKER_HOST values should log a sanitised warning."""
        import logging
        with caplog.at_level(logging.WARNING):
            _validate_docker_host("tcp://evil-internal:6379")
        assert "DOCKER_HOST" in caplog.text, "Warning log must mention DOCKER_HOST for operator visibility"
        # Must NOT log the full URI (could contain credentials)
        assert "evil-internal:6379" not in caplog.text, "Warning log must NOT contain the raw host (credential leak risk)"
