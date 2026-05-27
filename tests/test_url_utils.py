"""
Tests for lib.utils.url_utils.is_safe_url — SSRF URL validation.

Covers:
- Safe public URLs (HTTP/HTTPS)
- Blocked: localhost, 127.0.0.1, 0.0.0.0, ::1
- Blocked: private network ranges (10.x, 172.16-31.x, 192.168.x, 169.254.x)
- Blocked: IPv6 unique local (fc00::/7)
- Blocked: non-HTTP schemes (ftp, file, gopher, etc.)
- Edge cases: missing hostname, empty string
- DNS rebinding: hostnames resolving to private IPs are blocked
"""

import socket
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.utils.url_utils import is_safe_url


def test_safe_public_urls():
    """Public URLs should be allowed (DNS mocked — testing SSRF logic, not DNS)."""
    with patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
    ]):
        assert is_safe_url("https://www.example.com") is True
        assert is_safe_url("http://example.com/path?query=1") is True
        assert is_safe_url("https://api.github.com/repos/foo") is True
        assert is_safe_url("https://sub.domain.example.co.uk/page") is True


def test_blocked_localhost():
    """localhost and loopback IPs should be blocked."""
    assert is_safe_url("http://localhost") is False
    assert is_safe_url("http://localhost:8080/path") is False
    assert is_safe_url("https://127.0.0.1") is False
    assert is_safe_url("http://127.0.0.1:3000") is False
    assert is_safe_url("http://0.0.0.0") is False
    assert is_safe_url("http://[::1]:8080") is False


def test_blocked_private_ranges():
    """Private IP ranges should be blocked."""
    # 10.0.0.0/8
    assert is_safe_url("http://10.0.0.1") is False
    assert is_safe_url("http://10.255.255.255") is False
    # 172.16.0.0/12
    assert is_safe_url("http://172.16.0.1") is False
    assert is_safe_url("http://172.31.255.255") is False
    # 192.168.0.0/16
    assert is_safe_url("http://192.168.1.1") is False
    assert is_safe_url("http://192.168.255.255") is False
    # 169.254.0.0/16 (cloud metadata)
    assert is_safe_url("http://169.254.169.254") is False


def test_blocked_ipv6_ula():
    """IPv6 unique local addresses should be blocked."""
    assert is_safe_url("http://[fc00::1]") is False
    assert is_safe_url("http://[fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff]") is False


def test_blocked_non_http_schemes():
    """Non-HTTP/HTTPS schemes should be blocked."""
    assert is_safe_url("ftp://example.com") is False
    assert is_safe_url("file:///etc/passwd") is False
    assert is_safe_url("gopher://example.com") is False
    assert is_safe_url("javascript:alert(1)") is False


def test_edge_cases():
    """Edge cases should be blocked."""
    assert is_safe_url("") is False
    assert is_safe_url("not-a-url") is False
    assert is_safe_url("http://") is False


def test_dns_rebinding_cloud_metadata():
    """Hostname resolving to cloud metadata IP should be blocked."""
    with patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))
    ]):
        assert is_safe_url("http://metadata.internal") is False


def test_dns_rebinding_private_ip():
    """Hostname resolving to private IP (10.x) should be blocked."""
    with patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))
    ]):
        assert is_safe_url("http://neo4j.internal") is False


def test_dns_rebinding_private_ip_192():
    """Hostname resolving to 192.168.x.x should be blocked."""
    with patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.100", 0))
    ]):
        assert is_safe_url("http://admin-panel.local") is False


def test_dns_rebinding_safe_hostname():
    """Hostname resolving to public IP should be allowed."""
    with patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
    ]):
        assert is_safe_url("http://example.com") is True


def test_dns_rebinding_failed_resolution():
    """Hostname that fails DNS resolution should be blocked."""
    with patch("socket.getaddrinfo", side_effect=socket.gaierror):
        assert is_safe_url("http://nonexistent.invalid") is False


def test_dns_rebinding_mixed_ips():
    """Hostname with multiple resolved IPs, one private — should be blocked."""
    with patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
    ]):
        assert is_safe_url("http://dual.internal") is False


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
