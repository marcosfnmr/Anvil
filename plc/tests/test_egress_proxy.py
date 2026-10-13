"""Unit tests for strict HTTP CONNECT egress behavior."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from egress_proxy import _is_public_ipv4, _parse_connect_request, _resolve_public_ipv4, _validate_config


class EgressProxyTests(unittest.TestCase):
    def test_parses_exact_connect_authority_and_keeps_tunnel_bytes(self):
        parsed = _parse_connect_request(
            b"CONNECT Docs.Example.NET.:443 HTTP/1.1\r\nHost: ignored\r\n\r\nTLS"
        )
        self.assertEqual(parsed, ("docs.example.net", 443, b"TLS"))

    def test_rejects_non_connect_ip_literal_and_malformed_authorities(self):
        for request in (
            b"GET http://example.net/ HTTP/1.1\r\n\r\n",
            b"CONNECT 8.8.8.8:443 HTTP/1.1\r\n\r\n",
            b"CONNECT *.example.net:443 HTTP/1.1\r\n\r\n",
            b"CONNECT example.net:443\r\n\r\n",
            b"CONNECT example.net:0 HTTP/1.1\r\n\r\n",
            b"CONNECT example.net:443 HTTP/1.1\r\nContent-Length: 10\r\n\r\n",
        ):
            with self.subTest(request=request):
                self.assertIsNone(_parse_connect_request(request))

    def test_public_address_screen_blocks_non_public_ip_ranges(self):
        for address in ("8.8.8.8", "1.1.1.1"):
            self.assertTrue(_is_public_ipv4(address))
        for address in ("127.0.0.1", "10.0.0.1", "169.254.1.1", "192.168.1.1", "224.0.0.1"):
            self.assertFalse(_is_public_ipv4(address))

    def test_dns_resolution_pins_only_public_ipv4_results(self):
        public_result = [(None, None, None, None, ("8.8.8.8", 443))]
        resolver = Mock(return_value=public_result)
        self.assertEqual(_resolve_public_ipv4("docs.example.net", 443, resolver), ["8.8.8.8"])
        mixed = public_result + [(None, None, None, None, ("127.0.0.1", 443))]
        self.assertEqual(_resolve_public_ipv4("docs.example.net", 443, lambda *_args: mixed), [])

    def test_config_requires_specific_bindings_and_unique_destinations(self):
        config = _validate_config('{"listen_ip":"172.30.1.12","source_ip":"172.30.1.11",'
                                  '"rules":[{"hostname":"docs.example.net","tcp_port":443}]}')
        self.assertEqual(config["allowed_destinations"], {("docs.example.net", 443)})
        for raw in (
            '{"listen_ip":"0.0.0.0","source_ip":"172.30.1.11","rules":[{"hostname":"docs.example.net","tcp_port":443}]}',
            '{"listen_ip":"172.30.1.12","source_ip":"172.30.1.11","rules":[{"hostname":"127.0.0.1","tcp_port":443}]}',
            '{"listen_ip":"172.30.1.12","source_ip":"172.30.1.11","rules":[{"hostname":"docs.example.net","tcp_port":443},{"hostname":"docs.example.net","tcp_port":443}]}',
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                _validate_config(raw)


if __name__ == "__main__":
    unittest.main()
