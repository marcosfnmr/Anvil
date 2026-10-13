from __future__ import annotations

import sys
import asyncio
import random
import unittest
from pathlib import Path

PLC_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLC_DIR))

from modbus_schema import ModbusConfig  # noqa: E402
from modbus_server import _request_summary, create_context  # noqa: E402
from modbus_server import BehaviorRequestHandler, ServerRequestHandler  # noqa: E402
from modbus_server import BehaviorModbusSerialServer  # noqa: E402
from timing import DeterministicJitter  # noqa: E402


class ModbusServerContextTests(unittest.TestCase):
    def test_request_summary_describes_write_without_logging_value(self):
        from types import SimpleNamespace

        request = SimpleNamespace(function_code=6, dev_id=4, address=12, values=[54321])
        summary = _request_summary(request, ("10.0.0.8", 43000))
        self.assertIn("source=10.0.0.8", summary)
        self.assertIn("unit_id=4", summary)
        self.assertIn("function=6", summary)
        self.assertIn("operation=write_holding_register", summary)
        self.assertIn("space=holding_register address=12 count=1", summary)
        self.assertNotIn("54321", summary)

    def test_request_summary_counts_multiple_coil_values(self):
        from types import SimpleNamespace

        summary = _request_summary(
            SimpleNamespace(function_code=15, dev_id=1, address=20, count=0,
                            bits=[], registers=[], values=[True, False, True]),
            None,
            "10.0.0.9",
        )
        self.assertIn("source=10.0.0.9", summary)
        self.assertIn("operation=write_coils space=coil address=20 count=3", summary)

    def test_server_send_logs_semantic_result_without_values(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        handler = object.__new__(BehaviorRequestHandler)
        handler.last_pdu = SimpleNamespace(function_code=5, dev_id=2, address=8, values=[True])
        handler.transport = SimpleNamespace(get_extra_info=lambda key: ("192.0.2.10", 1200))
        parent_send = Mock()
        with patch("modbus_server.ServerRequestHandler.server_send", parent_send), \
                patch("modbus_server.LOGGER.info") as info:
            handler.server_send(SimpleNamespace(function_code=5, exception_code=0), None)
        parent_send.assert_called_once()
        logged = " ".join(str(part) for part in info.call_args.args)
        self.assertIn("operation=write_coil", logged)
        self.assertIn("result=%s", logged)
        self.assertIn("ok", logged)
        self.assertIn("source=192.0.2.10", logged)
        self.assertNotIn("True", logged)

    def test_server_send_persists_value_free_write_metadata(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from protocol_audit import ProtocolAuditQueue

        queue = ProtocolAuditQueue(":memory:")
        handler = object.__new__(BehaviorRequestHandler)
        handler.server = SimpleNamespace(protocol_audit_queue=queue)
        handler.last_pdu = SimpleNamespace(function_code=6, dev_id=4, address=12, values=[54321])
        handler.transport = SimpleNamespace(get_extra_info=lambda key: ("192.0.2.10", 1200))
        with patch("modbus_server.ServerRequestHandler.server_send", Mock()):
            handler.server_send(SimpleNamespace(function_code=6), None)
        event = queue.pending()["events"][0]["line"]
        self.assertIn("operation=write_holding_register", event)
        self.assertIn("address=12", event)
        self.assertNotIn("54321", event)
        queue.close()

    def test_rtu_server_uses_the_durable_value_free_audit_handler(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from protocol_audit import ProtocolAuditQueue

        queue = ProtocolAuditQueue(":memory:")

        async def create_server():
            server = BehaviorModbusSerialServer(
                create_context(ModbusConfig.model_validate({"transport": "rtu", "unit_id": 17,
                    "points": [{"type": "coil", "name": "pump", "address": 0}]})),
                port="/dev/pts/anvil-test",
                protocol_audit_queue=queue,
            )
            return server, server.callback_new_connection()

        server, handler = asyncio.run(create_server())
        try:
            handler.last_pdu = SimpleNamespace(function_code=5, dev_id=17, address=0, values=[True])
            handler.last_addr = None
            with patch("modbus_server.ServerRequestHandler.server_send", Mock()):
                handler.server_send(SimpleNamespace(function_code=5), None)
            event = queue.pending()["events"][0]["line"]
            self.assertIn("source=rtu-serial", event)
            self.assertIn("unit_id=17", event)
            self.assertIn("operation=write_coil", event)
            self.assertNotIn("True", event)
        finally:
            server.close()
            queue.close()

    def test_tcp_fault_probability_is_seeded_per_request(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock, patch

        async def exercise(seed):
            handler = object.__new__(BehaviorRequestHandler)
            handler.server = SimpleNamespace(
                response_delay_ms=0, response_jitter=DeterministicJitter(0),
                fault_mode="device_failure", fault_probability=0.5, fault_rng=random.Random(seed),
            )
            handler.behavior_lock = asyncio.Lock()
            handler.last_pdu = SimpleNamespace(function_code=1, transaction_id=1, dev_id=1)
            handler.last_addr = None
            handler.server_send = Mock()
            observed = []
            with patch("modbus_server.ServerRequestHandler.handle_request", new_callable=AsyncMock) as parent:
                for _ in range(20):
                    faults_before = handler.server_send.call_count
                    await handler.handle_request()
                    observed.append(handler.server_send.call_count > faults_before)
            return observed, parent.await_count

        observed, passed_through = asyncio.run(exercise(29))
        expected_rng = random.Random(29)
        self.assertEqual(observed, [expected_rng.random() < 0.5 for _ in range(20)])
        self.assertEqual(passed_through, 20 - sum(observed))

    def test_tcp_handler_applies_repeatable_bounded_jitter_after_fixed_delay(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        async def exercise():
            handler = object.__new__(BehaviorRequestHandler)
            handler.server = SimpleNamespace(
                response_delay_ms=20,
                response_jitter=DeterministicJitter(50),
                fault_mode="none",
            )
            handler.behavior_lock = asyncio.Lock()
            handler.last_pdu = object()
            handler.last_addr = None
            with patch("modbus_server.ServerRequestHandler.handle_request", new_callable=AsyncMock) as parent, \
                    patch("modbus_server.asyncio.sleep", new_callable=AsyncMock) as sleep:
                await handler.handle_request()
                await handler.handle_request()
            return [call.args[0] for call in sleep.call_args_list], parent.await_count

        delays, handled = asyncio.run(exercise())
        expected = DeterministicJitter(50)
        self.assertEqual(delays, [(20 + expected.next_ms()) / 1000 for _ in range(2)])
        self.assertEqual(handled, 2)

    def test_tcp_handler_uses_configured_statistical_jitter_profile(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        async def exercise(seed):
            handler = object.__new__(BehaviorRequestHandler)
            handler.server = SimpleNamespace(response_delay_ms=0,
                response_jitter=DeterministicJitter(60, seed=seed, distribution="normal"), fault_mode="none")
            handler.behavior_lock = asyncio.Lock()
            handler.last_pdu = object()
            handler.last_addr = None
            with patch("modbus_server.ServerRequestHandler.handle_request", new_callable=AsyncMock), \
                    patch("modbus_server.asyncio.sleep", new_callable=AsyncMock) as sleep:
                for _ in range(20):
                    await handler.handle_request()
            return [round(call.args[0] * 1000) for call in sleep.call_args_list]

        first, repeat, other = asyncio.run(exercise(5)), asyncio.run(exercise(5)), asyncio.run(exercise(6))
        self.assertEqual(first, repeat)
        self.assertNotEqual(first, other)
        self.assertTrue(all(0 <= value <= 60 for value in first))

    def test_uses_zero_based_protocol_addresses_and_separate_spaces(self) -> None:
        config = ModbusConfig.model_validate({"unit_id": 5, "points": [
            {"type": "coil", "name": "motor", "address": 0, "initial_value": True},
            {"type": "discrete_input", "name": "switch", "address": 0, "initial_value": True},
            {"type": "holding_register", "name": "speed", "address": 0, "initial_value": 125},
            {"type": "input_register", "name": "pressure", "address": 7, "initial_value": 42},
        ]})
        context = create_context(config)
        device = context[5]
        self.assertEqual(device.getValues(1, 0, 1), [True])
        self.assertEqual(device.getValues(2, 0, 1), [True])
        self.assertEqual(device.getValues(3, 0, 1), [125])
        self.assertEqual(device.getValues(4, 7, 1), [42])
        device.setValues(5, 0, [False])
        self.assertEqual(device.getValues(1, 0, 1), [False])
        self.assertEqual(device.getValues(2, 0, 1), [True])

    def test_discrete_input_points_use_independent_addresses_and_boolean_values(self):
        config = ModbusConfig.model_validate({"points": [
            {"type": "coil", "name": "output", "address": 3, "initial_value": False},
            {"type": "discrete_input", "name": "input", "address": 3, "initial_value": True},
        ]})
        device = create_context(config)[config.unit_id]
        self.assertEqual(device.getValues(1, 3, 1), [False])
        self.assertEqual(device.getValues(2, 3, 1), [True])

    def test_rtu_transport_validates_serial_line_parameters(self) -> None:
        config = ModbusConfig.model_validate({"transport": "rtu", "unit_id": 17, "port": 1502,
                                              "baudrate": 9600, "bytesize": 8, "parity": "N", "stopbits": 1,
                                              "points": [{"type": "holding_register", "name": "speed", "address": 4}]})
        self.assertEqual((config.transport, config.baudrate, config.parity), ("rtu", 9600, "N"))
        for payload in (
            {"transport": "rtu", "unit_id": 0, "points": [{"type": "coil", "name": "c", "address": 0}]},
            {"transport": "rtu", "parity": "E", "points": [{"type": "coil", "name": "c", "address": 0}]},
            {"transport": "rtu", "fault_mode": "disconnect", "points": [{"type": "coil", "name": "c", "address": 0}]},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ModbusConfig.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
