"""
url_utils.py — Shared URL validation utilities.

Provides SSRF protection for any component that fetches external URLs
(vision service, tool wrappers, etc.).
"""
import ipaddress
import socket
from urllib.parse import urlparse

# Private/loopback hosts blocked for SSRF protection
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}

# Private, link-local, and cloud-metadata network ranges
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # cloud metadata (AWS, GCP, Azure)
    ipaddress.ip_network("fc00::/7"),         # IPv6 unique local addresses
]


def is_safe_url(url: str) -> bool:
    """
    Validate that a URL does not target internal or private hosts.

    Blocks:
    - Non-HTTP/HTTPS schemes
    - Loopback / well-known private hosts
    - Private IPv4 ranges (10.x, 172.16-31.x, 192.168.x)
    - Cloud metadata range (169.254.x)
    - IPv6 unique-local (fc00::/7)
    - Hostnames that resolve to any of the above (DNS-rebinding protection)

    Args:
        url: The URL to validate.

    Returns:
        True if the URL is safe to fetch, False otherwise.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname or hostname.lower() in _BLOCKED_HOSTS:
        return False

    try:
        addr = ipaddress.ip_address(hostname)
        return not any(addr in net for net in _BLOCKED_NETWORKS)
    except ValueError:
        # Hostname (not a bare IP) — resolve DNS and check every returned address
        try:
            resolved = socket.getaddrinfo(hostname, None)
            for r in resolved:
                addr = ipaddress.ip_address(r[4][0])
                if any(addr in net for net in _BLOCKED_NETWORKS):
                    return False
        except socket.gaierror:
            return False
        return True
