import inspect
import socket
import unittest
from unittest.mock import patch

from infrastructure.research.web_gateway import (
    ResearchWebGateway,
    UnsafeUrlError,
    _validate_public_url,
)


def _address(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


class ResearchWebSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_non_http_protocols(self):
        for url in ("file:///C:/secret", "ftp://example.com/a"):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeUrlError):
                    await _validate_public_url(url)

    async def test_rejects_url_credentials(self):
        with self.assertRaises(UnsafeUrlError):
            await _validate_public_url("https://user:pass@example.com")

    async def test_rejects_private_and_metadata_addresses(self):
        for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254"):
            with self.subTest(ip=ip), patch(
                "socket.getaddrinfo", return_value=_address(ip)
            ):
                with self.assertRaises(UnsafeUrlError):
                    await _validate_public_url("https://example.com")

    async def test_accepts_public_address(self):
        with patch(
            "socket.getaddrinfo", return_value=_address("93.184.216.34")
        ):
            await _validate_public_url("https://example.com/article")

    def test_fetch_streams_and_limits_redirects(self):
        source = inspect.getsource(ResearchWebGateway._download)
        self.assertIn('client.stream("GET", current_url)', source)
        self.assertIn("aiter_bytes", source)
        self.assertIn("for _ in range(4)", source)
        self.assertIn("await _validate_public_url(current_url)", source)

    def test_fetch_checks_mime_and_size(self):
        source = inspect.getsource(ResearchWebGateway._download)
        self.assertIn("content-type", source)
        self.assertIn("content-length", source)
        self.assertIn("total > max_bytes", source)