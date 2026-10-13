from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingress_proxy import (  # noqa: E402
    AUTO_DEFAULT_GATEWAY, MAX_CONCURRENT_SESSIONS, _default_gateway_ipv4,
    _dispatch_connection, _resolve_allowed_peer,
)


class IngressProxyGatewayTests(unittest.TestCase):
    def test_resolves_little_endian_ipv4_default_route(self) -> None:
        route_table = "Iface Destination Gateway Flags RefCnt Use Metric Mask\neth0 00000000 01001EAC 0003 0 0 0 00000000\n"
        self.assertEqual(_default_gateway_ipv4(route_table), "172.30.0.1")

    def test_accepts_a_valid_unicast_gateway_outside_private_ranges(self) -> None:
        route_table = "Iface Destination Gateway Flags RefCnt Use Metric Mask\neth0 00000000 01080808 0003 0 0 0 00000000\n"
        self.assertEqual(_default_gateway_ipv4(route_table), "8.8.8.1")

    def test_requires_a_valid_default_gateway_route(self) -> None:
        for route_table in (
            "Iface Destination Gateway Flags RefCnt Use Metric Mask\neth0 00000000 00000000 0001 0 0 0 00000000\n",
            "Iface Destination Gateway Flags RefCnt Use Metric Mask\neth0 01001EAC 01001EAC 0003 0 0 0 FFFFFFFF\n",
            "Iface Destination Gateway Flags RefCnt Use Metric Mask\neth0 00000000 01001EAC 0001 0 0 0 00000000\n",
        ):
            with self.assertRaisesRegex(RuntimeError, "No IPv4 default gateway"):
                _default_gateway_ipv4(route_table)

    def test_resolves_auto_peer_and_preserves_explicit_runtime_peer(self) -> None:
        route_table = "Iface Destination Gateway Flags RefCnt Use Metric Mask\neth0 00000000 01001EAC 0003 0 0 0 00000000\n"
        self.assertEqual(_resolve_allowed_peer({"allowed_peer_ip": AUTO_DEFAULT_GATEWAY}, route_table), "172.30.0.1")
        self.assertEqual(_resolve_allowed_peer({"allowed_peer_ip": "172.18.0.1"}, route_table), "172.18.0.1")

    def test_ingress_rejects_connections_when_the_bounded_session_pool_is_full(self) -> None:
        slots = threading.BoundedSemaphore(1)
        pending_workers = []

        class DeferredThread:
            def __init__(self, *, target, args, daemon):
                self.target, self.args, self.daemon = target, args, daemon

            def start(self):
                pending_workers.append(self)

        first = Mock()
        second = Mock()
        with patch("ingress_proxy.threading.Thread", DeferredThread):
            self.assertTrue(_dispatch_connection(first, "203.0.113.10", {}, slots))
            self.assertFalse(_dispatch_connection(second, "203.0.113.11", {}, slots))
            second.close.assert_called_once_with()
            self.assertEqual(len(pending_workers), 1)
            with patch("ingress_proxy._serve") as serve:
                pending_workers[0].target(*pending_workers[0].args)
                serve.assert_called_once_with(first, "203.0.113.10", {})
            third = Mock()
            self.assertTrue(_dispatch_connection(third, "203.0.113.12", {}, slots))

    def test_ingress_connection_limit_is_fixed_and_bounded(self) -> None:
        self.assertEqual(MAX_CONCURRENT_SESSIONS, 64)

    def test_ingress_releases_a_session_slot_when_relay_raises(self) -> None:
        slots = threading.BoundedSemaphore(1)
        pending_workers = []

        class DeferredThread:
            def __init__(self, *, target, args, daemon):
                self.target, self.args = target, args

            def start(self):
                pending_workers.append(self)

        with patch("ingress_proxy.threading.Thread", DeferredThread):
            self.assertTrue(_dispatch_connection(Mock(), "203.0.113.20", {}, slots))
            with patch("ingress_proxy._serve", side_effect=RuntimeError("relay failed")):
                with self.assertRaisesRegex(RuntimeError, "relay failed"):
                    pending_workers[0].target(*pending_workers[0].args)
            self.assertTrue(_dispatch_connection(Mock(), "203.0.113.21", {}, slots))


if __name__ == "__main__":
    unittest.main()
