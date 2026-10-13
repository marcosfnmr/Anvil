from __future__ import annotations

import unittest

from modbus_interceptor import (
    ModbusFrameError,
    ModbusTransactionCorrelator,
    RewriteRule,
    decode_read_request,
    rewrite_read_response,
    rewrite_single_write_request,
    rewrite_write_request,
    split_mbap_frame,
)


def _request(transaction: int = 7, unit: int = 1, function: int = 3,
             start: int = 10, quantity: int = 2) -> bytes:
    return (
        transaction.to_bytes(2, "big") + b"\x00\x00\x00\x06"
        + bytes((unit, function)) + start.to_bytes(2, "big") + quantity.to_bytes(2, "big")
    )


def _response(transaction: int = 7, unit: int = 1, function: int = 3,
              values: tuple[int, ...] = (100, 200)) -> bytes:
    data = b"".join(value.to_bytes(2, "big") for value in values)
    length = 3 + len(data)
    return transaction.to_bytes(2, "big") + b"\x00\x00" + length.to_bytes(2, "big") + bytes(
        (unit, function, len(data))
    ) + data


def _write_request(transaction: int = 9, unit: int = 1, address: int = 10, value: int = 1) -> bytes:
    return (transaction.to_bytes(2, "big") + b"\x00\x00\x00\x06"
            + bytes((unit, 6)) + address.to_bytes(2, "big") + value.to_bytes(2, "big"))


def _single_coil_write_request(transaction: int = 12, unit: int = 1, address: int = 10,
                               value: int = 0xFF00) -> bytes:
    return (transaction.to_bytes(2, "big") + b"\x00\x00\x00\x06"
            + bytes((unit, 5)) + address.to_bytes(2, "big") + value.to_bytes(2, "big"))


def _mask_write_request(transaction: int = 13, unit: int = 1, address: int = 10,
                        and_mask: int = 0xFF00, or_mask: int = 0x00F0) -> bytes:
    return (transaction.to_bytes(2, "big") + b"\x00\x00\x00\x08"
            + bytes((unit, 22)) + address.to_bytes(2, "big")
            + and_mask.to_bytes(2, "big") + or_mask.to_bytes(2, "big"))


def _read_write_request(transaction: int = 14, unit: int = 1, read_start: int = 10,
                        read_quantity: int = 2, write_start: int = 20,
                        write_values: tuple[int, ...] = (1234,)) -> bytes:
    data = b"".join(value.to_bytes(2, "big") for value in write_values)
    pdu = (bytes((unit, 23)) + read_start.to_bytes(2, "big")
           + read_quantity.to_bytes(2, "big") + write_start.to_bytes(2, "big")
           + len(write_values).to_bytes(2, "big") + bytes((len(data),)) + data)
    return transaction.to_bytes(2, "big") + b"\x00\x00" + len(pdu).to_bytes(2, "big") + pdu


def _multiple_write_request(transaction: int = 10, unit: int = 1, address: int = 10,
                            values: tuple[int, ...] = (1, 2, 3)) -> bytes:
    data = b"".join(value.to_bytes(2, "big") for value in values)
    pdu = bytes((unit, 16)) + address.to_bytes(2, "big") + len(values).to_bytes(2, "big")
    pdu += bytes((len(data),)) + data
    return transaction.to_bytes(2, "big") + b"\x00\x00" + (len(pdu)).to_bytes(2, "big") + pdu


def _multiple_coil_write_request(transaction: int = 11, unit: int = 1, address: int = 10,
                                 values: tuple[int, ...] = (1, 0, 1)) -> bytes:
    data = bytearray((len(values) + 7) // 8)
    for index, value in enumerate(values):
        if value:
            data[index // 8] |= 1 << (index % 8)
    pdu = bytes((unit, 15)) + address.to_bytes(2, "big") + len(values).to_bytes(2, "big")
    pdu += bytes((len(data),)) + data
    return transaction.to_bytes(2, "big") + b"\x00\x00" + len(pdu).to_bytes(2, "big") + pdu


class ModbusInterceptorTests(unittest.TestCase):
    def test_fc23_decodes_both_ranges_and_preserves_the_combined_request(self) -> None:
        request = _read_write_request(read_start=10, read_quantity=2, write_start=30,
                                      write_values=(0x1234, 0x5678))
        decoded = decode_read_request(request)
        self.assertEqual((decoded.function_code, decoded.start_address, decoded.quantity), (23, 10, 2))
        self.assertEqual(decoded.write_values, ())
        relay = ModbusTransactionCorrelator([RewriteRule("mixed-read", 1, 23, 11, 0xA55A)])
        self.assertEqual(relay.forward_request(request), request)
        response = _response(transaction=14, function=23, values=(0x1111, 0x2222))
        rewritten, matched = relay.forward_response(response)
        self.assertEqual(matched, ("mixed-read",))
        self.assertEqual(rewritten[9:11], b"\x11\x11")
        self.assertEqual(rewritten[11:13], b"\xA5\x5A")
        self.assertEqual(relay.pending, {})

    def test_fc23_write_phase_rewrites_only_selected_write_register(self) -> None:
        request = _read_write_request(write_start=30, write_values=(100, 200, 300))
        relay = ModbusTransactionCorrelator([
            RewriteRule("write-middle", 1, 23, 31, 65000, phase="write"),
            RewriteRule("read-first", 1, 23, 10, 12345),
        ])
        forwarded, matches = relay.forward_request_with_matches(request)
        self.assertEqual(matches, ("write-middle",))
        self.assertEqual(forwarded[17:23], b"\x00\x64\xfd\xe8\x01\x2c")
        self.assertEqual(forwarded[:17], request[:17])
        self.assertEqual(forwarded[21:], request[21:])
        response = _response(transaction=14, function=23, values=(100, 200))
        rewritten, matches = relay.forward_response(response)
        self.assertEqual(matches, ("read-first",))
        self.assertEqual(rewritten[9:13], b"\x30\x39\x00\xc8")

    def test_fc23_write_phase_ignores_other_ranges_and_rejects_duplicates(self) -> None:
        request = _read_write_request(write_start=30, write_values=(100, 200))
        decoded = decode_read_request(request)
        other = [RewriteRule("other-unit", 2, 23, 30, 1, phase="write"),
                 RewriteRule("outside", 1, 23, 32, 1, phase="write"),
                 RewriteRule("read-only", 1, 23, 30, 1)]
        self.assertEqual(rewrite_write_request(request, decoded, other), (request, ()))
        duplicate = [RewriteRule("first", 1, 23, 30, 1, phase="write"),
                     RewriteRule("second", 1, 23, 30, 2, phase="write")]
        with self.assertRaisesRegex(ValueError, "same FC23 write register"):
            rewrite_write_request(request, decoded, duplicate)
        with self.assertRaisesRegex(ValueError, "phase"):
            ModbusTransactionCorrelator().replace_rules([
                RewriteRule("invalid", 1, 6, 30, 1, phase="write")])

    def test_fc23_rejects_malformed_read_write_quantities_and_ranges(self) -> None:
        for request in (
            _read_write_request(read_quantity=0),
            _read_write_request(write_values=()),
            _read_write_request(read_start=65_535, read_quantity=2),
            _read_write_request(write_start=65_535, write_values=(1, 2)),
        ):
            with self.subTest(request=request):
                with self.assertRaises(ModbusFrameError):
                    decode_read_request(request)
        malformed = bytearray(_read_write_request())
        malformed[16] = 4
        with self.assertRaisesRegex(ModbusFrameError, "byte count"):
            decode_read_request(bytes(malformed))

    def test_mbap_split_handles_partial_concatenated_and_complete_frames(self) -> None:
        first, second = _request(), _request(transaction=8, function=4)
        self.assertIsNone(split_mbap_frame(first[:5]))
        self.assertEqual(split_mbap_frame(first[:8]), None)
        self.assertEqual(split_mbap_frame(first + second), (first, second))

    def test_mbap_rejects_invalid_protocol_identifier_and_length(self) -> None:
        with self.assertRaisesRegex(ModbusFrameError, "identifier"):
            split_mbap_frame(b"\x00\x01\x00\x01\x00\x06")
        with self.assertRaisesRegex(ModbusFrameError, "length"):
            split_mbap_frame(b"\x00\x01\x00\x00\x00\x01")

    def test_decode_request_accepts_fc03_fc04_and_fc06(self) -> None:
        self.assertEqual(decode_read_request(_request(function=4)).function_code, 4)
        self.assertEqual(decode_read_request(_write_request()).function_code, 6)
        self.assertEqual(decode_read_request(_single_coil_write_request()).write_values, (1,))
        self.assertEqual(decode_read_request(_multiple_write_request()).quantity, 3)
        self.assertEqual(decode_read_request(_multiple_coil_write_request()).quantity, 3)
        with self.assertRaisesRegex(ModbusFrameError, "range"):
            decode_read_request(_request(start=65_535, quantity=2))

    def test_fc01_and_fc02_rewrite_only_the_selected_response_bits(self) -> None:
        for function_code, rule_id, replacement in ((1, "mask-coil", 0), (2, "assert-input", 1)):
            request = _request(transaction=21 + function_code, function=function_code,
                               start=8, quantity=10)
            decoded = decode_read_request(request)
            self.assertEqual((decoded.function_code, decoded.start_address, decoded.quantity),
                             (function_code, 8, 10))
            # Two data bytes: requested bit 0 and bit 1 in the second byte
            # are real points; remaining upper bits are protocol padding.
            response = (request[:4] + b"\x00\x05" + bytes((1, function_code, 2, 0xFF, 0xFE)))
            rule = RewriteRule(rule_id, 1, function_code, 8, replacement)
            relay = ModbusTransactionCorrelator([rule])
            relay.forward_request(request)
            rewritten, matched = relay.forward_response(response)
            expected = 0xFE if replacement == 0 else 0xFF
            self.assertEqual(matched, (rule_id,))
            self.assertEqual(rewritten[9], expected)
            self.assertEqual(rewritten[10], 0xFE, "unselected data bits and padding stay unchanged")

    def test_fc01_fc02_validate_quantity_response_size_and_boolean_replacements(self) -> None:
        for function_code in (1, 2):
            with self.subTest(function_code=function_code):
                with self.assertRaisesRegex(ModbusFrameError, "quantity"):
                    decode_read_request(_request(function=function_code, quantity=0))
                with self.assertRaisesRegex(ModbusFrameError, "quantity"):
                    decode_read_request(_request(function=function_code, quantity=2001))
                with self.assertRaisesRegex(ValueError, "bit replacement must be 0 or 1"):
                    ModbusTransactionCorrelator().replace_rules([RewriteRule("bad-bit", 1, function_code, 0, 2)])
                request = _request(function=function_code, quantity=9)
                relay = ModbusTransactionCorrelator([RewriteRule("bit", 1, function_code, 0, 1)])
                relay.forward_request(request)
                malformed = request[:4] + b"\x00\x04" + bytes((1, function_code, 1, 0))
                with self.assertRaisesRegex(ModbusFrameError, "length does not match"):
                    relay.forward_response(malformed)

    def test_fc15_rewrites_only_selected_coils_in_multiple_write_request(self) -> None:
        request = _multiple_coil_write_request(values=(1, 1, 0, 1, 1, 0, 1, 1, 0))
        decoded = decode_read_request(request)
        rules = [RewriteRule("turn-off-second", 1, 15, 11, 0),
                 RewriteRule("turn-on-last", 1, 15, 18, 1)]
        rewritten, matched = rewrite_write_request(request, decoded, rules)
        self.assertEqual(matched, ("turn-off-second", "turn-on-last"))
        self.assertEqual(rewritten[:13], request[:13])
        self.assertEqual([((rewritten[13 + index // 8] >> (index % 8)) & 1)
                          for index in range(9)], [1, 0, 0, 1, 1, 0, 1, 1, 1])
        for other in (RewriteRule("wrong-unit", 2, 15, 11, 0),
                      RewriteRule("wrong-fc", 1, 16, 11, 0),
                      RewriteRule("outside", 1, 15, 19, 0)):
            self.assertEqual(rewrite_write_request(request, decoded, [other]), (request, ()))

    def test_fc15_rejects_malformed_quantity_byte_count_and_replacement(self) -> None:
        malformed = bytearray(_multiple_coil_write_request())
        malformed[12] = 2
        with self.assertRaisesRegex(ModbusFrameError, "quantity or byte count"):
            decode_read_request(bytes(malformed))
        request = _multiple_coil_write_request()
        decoded = decode_read_request(request)
        with self.assertRaisesRegex(ValueError, "must be 0 or 1"):
            rewrite_write_request(request, decoded, [RewriteRule("invalid", 1, 15, 10, 2)])

    def test_fc05_rewrites_only_exact_single_coil_and_validates_wire_encoding(self) -> None:
        request = _single_coil_write_request(address=10, value=0xFF00)
        decoded = decode_read_request(request)
        rule = RewriteRule("turn-off-coil", 1, 5, 10, 0)
        rewritten, matched = rewrite_write_request(request, decoded, [rule])
        self.assertEqual(matched, ("turn-off-coil",))
        self.assertEqual(rewritten[:10], request[:10])
        self.assertEqual(rewritten[10:12], b"\x00\x00")
        for other in (RewriteRule("unit", 2, 5, 10, 0),
                      RewriteRule("address", 1, 5, 11, 0),
                      RewriteRule("function", 1, 15, 10, 0)):
            self.assertEqual(rewrite_write_request(request, decoded, [other]), (request, ()))
        with self.assertRaisesRegex(ModbusFrameError, "0x0000 or 0xFF00"):
            decode_read_request(_single_coil_write_request(value=1))
        with self.assertRaisesRegex(ValueError, "must be 0 or 1"):
            rewrite_write_request(request, decoded, [RewriteRule("invalid", 1, 5, 10, 2)])

    def test_correlator_tracks_fc05_rewrite_and_validates_echo(self) -> None:
        request = _single_coil_write_request(value=0xFF00)
        relay = ModbusTransactionCorrelator([RewriteRule("close-coil", 1, 5, 10, 0)])
        forwarded, matched = relay.forward_request_with_matches(request)
        self.assertEqual(matched, ("close-coil",))
        response = forwarded
        self.assertEqual(relay.forward_response(response), (response, ()))
        self.assertEqual(relay.pending, {})

        relay.forward_request_with_matches(request)
        bad_echo = bytearray(response)
        bad_echo[11] = 1
        with self.assertRaisesRegex(ModbusFrameError, "echo does not match"):
            relay.forward_response(bytes(bad_echo))

    def test_fc22_rewrites_mask_to_force_exact_register_and_validates_echo(self) -> None:
        request = _mask_write_request(address=10, and_mask=0x0F0F, or_mask=0xF000)
        decoded = decode_read_request(request)
        self.assertEqual(decoded.function_code, 22)
        self.assertEqual(decoded.write_values, (0x0F0F, 0xF000))
        rule = RewriteRule("force-register", 1, 22, 10, 0xA55A)
        rewritten, matched = rewrite_write_request(request, decoded, [rule])
        self.assertEqual(matched, ("force-register",))
        self.assertEqual(rewritten[:10], request[:10])
        self.assertEqual(rewritten[10:12], b"\x00\x00")
        self.assertEqual(rewritten[12:14], b"\xA5\x5A")
        for other in (RewriteRule("unit", 2, 22, 10, 1),
                      RewriteRule("address", 1, 22, 11, 1),
                      RewriteRule("function", 1, 16, 10, 1)):
            self.assertEqual(rewrite_write_request(request, decoded, [other]), (request, ()))

        relay = ModbusTransactionCorrelator([rule])
        forwarded, matched = relay.forward_request_with_matches(request)
        self.assertEqual(matched, ("force-register",))
        self.assertEqual(relay.forward_response(forwarded), (forwarded, ()))
        relay.forward_request_with_matches(request)
        bad_echo = bytearray(forwarded)
        bad_echo[13] ^= 1
        with self.assertRaisesRegex(ModbusFrameError, "echo does not match"):
            relay.forward_response(bytes(bad_echo))

    def test_fc22_rejects_extra_or_missing_mask_bytes(self) -> None:
        request = _mask_write_request()
        missing = bytearray(request[:-1])
        missing[4:6] = b"\x00\x07"
        with self.assertRaisesRegex(ModbusFrameError, "exactly 14 bytes"):
            decode_read_request(bytes(missing))
        extra = bytearray(request + b"\x00")
        extra[4:6] = b"\x00\x09"
        with self.assertRaisesRegex(ModbusFrameError, "exactly 14 bytes"):
            decode_read_request(bytes(extra))

    def test_correlator_validates_fc15_write_echo_and_tracks_request_match(self) -> None:
        relay = ModbusTransactionCorrelator([RewriteRule("open-coil", 1, 15, 11, 1)])
        request = _multiple_coil_write_request(values=(0, 0, 0))
        forwarded, matched = relay.forward_request_with_matches(request)
        self.assertEqual(matched, ("open-coil",))
        self.assertEqual((forwarded[13] >> 1) & 1, 1)
        response = request[:4] + b"\x00\x06" + request[6:12]
        self.assertEqual(relay.forward_response(response), (response, ()))
        self.assertEqual(relay.pending, {})

    def test_fc16_rewrites_only_matching_registers_in_multiple_write_request(self) -> None:
        request = _multiple_write_request(values=(11, 22, 33))
        decoded = decode_read_request(request)
        rules = [RewriteRule("first", 1, 16, 10, 1001),
                 RewriteRule("last", 1, 16, 12, 1003)]
        rewritten, matched = rewrite_write_request(request, decoded, rules)
        self.assertEqual(matched, ("first", "last"))
        self.assertEqual(rewritten[:13], request[:13])
        self.assertEqual([int.from_bytes(rewritten[offset:offset + 2], "big")
                          for offset in (13, 15, 17)], [1001, 22, 1003])
        for other in (RewriteRule("unit", 2, 16, 10, 900),
                      RewriteRule("outside", 1, 16, 13, 900),
                      RewriteRule("wrong-function", 1, 6, 10, 900)):
            self.assertEqual(rewrite_write_request(request, decoded, [other]), (request, ()))

    def test_fc16_rejects_invalid_quantity_byte_count_and_address_range(self) -> None:
        invalid_quantity = bytearray(_multiple_write_request(values=(1,)))
        invalid_quantity[10:12] = b"\x00\x00"
        with self.assertRaisesRegex(ModbusFrameError, "quantity or byte count"):
            decode_read_request(bytes(invalid_quantity))
        malformed = bytearray(_multiple_write_request())
        malformed[12] -= 2
        with self.assertRaisesRegex(ModbusFrameError, "quantity or byte count"):
            decode_read_request(bytes(malformed))
        with self.assertRaisesRegex(ModbusFrameError, "range"):
            decode_read_request(_multiple_write_request(address=65_535, values=(1, 2)))

    def test_fc06_rewrites_only_the_exact_single_holding_register_request(self) -> None:
        request = _write_request()
        decoded = decode_read_request(request)
        rule = RewriteRule("pressure-write", 1, 6, 10, 65000)
        rewritten, matched = rewrite_single_write_request(request, decoded, [rule])
        self.assertEqual(matched, ("pressure-write",))
        self.assertEqual(rewritten[:10], request[:10])
        self.assertEqual(int.from_bytes(rewritten[10:12], "big"), 65000)
        for other in (
            RewriteRule("unit", 2, 6, 10, 65000),
            RewriteRule("address", 1, 6, 11, 65000),
            RewriteRule("read", 1, 3, 10, 65000),
        ):
            self.assertEqual(rewrite_single_write_request(request, decoded, [other]), (request, ()))

    def test_rewrite_changes_only_exact_register_and_preserves_other_bytes(self) -> None:
        request = decode_read_request(_request())
        response = _response()
        rule = RewriteRule("pressure", 1, 3, 11, 65_000)
        rewritten, matched = rewrite_read_response(response, request, [rule])
        self.assertEqual(matched, ("pressure",))
        self.assertEqual(rewritten[:11], response[:11])
        self.assertEqual(int.from_bytes(rewritten[9:11], "big"), 100)
        self.assertEqual(int.from_bytes(rewritten[11:13], "big"), 65_000)
        self.assertEqual(rewritten[13:], response[13:])

    def test_wrong_transaction_unit_function_and_out_of_range_rule_pass_unchanged(self) -> None:
        request = decode_read_request(_request())
        response = _response(transaction=8)
        rule = RewriteRule("pressure", 1, 3, 10, 65_000)
        self.assertEqual(rewrite_read_response(response, request, [rule]), (response, ()))
        response = _response()
        for other in (
            RewriteRule("unit", 2, 3, 10, 65_000),
            RewriteRule("function", 1, 4, 10, 65_000),
            RewriteRule("address", 1, 3, 12, 65_000),
        ):
            self.assertEqual(rewrite_read_response(response, request, [other]), (response, ()))

    def test_exception_response_and_nonmatching_frame_are_not_modified(self) -> None:
        request = decode_read_request(_request(function=4, quantity=1))
        exception = b"\x00\x07\x00\x00\x00\x03\x01\x84\x02"
        self.assertEqual(
            rewrite_read_response(exception, request, [RewriteRule("x", 1, 4, 10, 5)]),
            (exception, ()),
        )

    def test_malformed_read_response_fails_closed(self) -> None:
        request = decode_read_request(_request())
        response = bytearray(_response())
        response[8] = 2
        response[4:6] = (5).to_bytes(2, "big")
        with self.assertRaisesRegex(ModbusFrameError, "does not match"):
            rewrite_read_response(bytes(response), request, [])

    def test_duplicate_rules_for_one_register_are_rejected(self) -> None:
        request = decode_read_request(_request(quantity=1))
        response = _response(values=(100,))
        rules = [RewriteRule("a", 1, 3, 10, 1), RewriteRule("b", 1, 3, 10, 2)]
        with self.assertRaisesRegex(ValueError, "multiple rewrite rules"):
            rewrite_read_response(response, request, rules)

    def test_correlator_handles_interleaved_transactions_and_consumes_responses(self) -> None:
        relay = ModbusTransactionCorrelator([
            RewriteRule("pressure", 1, 3, 10, 900),
            RewriteRule("temperature", 1, 4, 20, 700),
        ])
        read_holding = _request(transaction=12, function=3, start=10, quantity=1)
        read_input = _request(transaction=13, function=4, start=20, quantity=1)
        self.assertEqual(relay.forward_request(read_holding), read_holding)
        self.assertEqual(relay.forward_request(read_input), read_input)

        input_response = _response(transaction=13, function=4, values=(42,))
        holding_response = _response(transaction=12, function=3, values=(21,))
        rewritten, matched = relay.forward_response(input_response)
        self.assertEqual(matched, ("temperature",))
        self.assertEqual(int.from_bytes(rewritten[9:11], "big"), 700)
        rewritten, matched = relay.forward_response(holding_response)
        self.assertEqual(matched, ("pressure",))
        self.assertEqual(int.from_bytes(rewritten[9:11], "big"), 900)
        self.assertEqual(relay.pending, {})
        # A duplicate/late response has no retained request and stays unchanged.
        self.assertEqual(relay.forward_response(holding_response), (holding_response, ()))

    def test_correlator_bounds_outstanding_requests_and_rejects_reused_ids(self) -> None:
        relay = ModbusTransactionCorrelator(max_pending=1)
        first = _request(transaction=1, quantity=1)
        relay.forward_request(first)
        with self.assertRaisesRegex(ModbusFrameError, "already awaiting"):
            relay.forward_request(first)
        with self.assertRaisesRegex(ModbusFrameError, "too many outstanding"):
            relay.forward_request(_request(transaction=2, quantity=1))

    def test_correlator_keeps_non_read_functions_transparent(self) -> None:
        relay = ModbusTransactionCorrelator([RewriteRule("x", 1, 3, 10, 9)])
        write_single = b"\x00\x20\x00\x00\x00\x06\x01\x06\x00\x0a\x00\x01"
        self.assertEqual(relay.forward_request(write_single), write_single)
        response = write_single
        self.assertEqual(relay.forward_response(response), (response, ()))

    def test_correlator_rewrites_fc06_request_and_tracks_server_echo(self) -> None:
        relay = ModbusTransactionCorrelator([RewriteRule("pressure-write", 1, 6, 10, 65000)])
        original = _write_request(value=42)
        forwarded, matched = relay.forward_request_with_matches(original)
        self.assertEqual(matched, ("pressure-write",))
        self.assertEqual(int.from_bytes(forwarded[10:12], "big"), 65000)
        response, matched = relay.forward_response(forwarded)
        self.assertEqual(response, forwarded)
        self.assertEqual(matched, ())
        self.assertEqual(relay.pending, {})

    def test_correlator_clears_fc06_exception_without_claiming_success(self) -> None:
        relay = ModbusTransactionCorrelator([RewriteRule("pressure-write", 1, 6, 10, 65000)])
        request = _write_request(value=42)
        forwarded, matched = relay.forward_request_with_matches(request)
        self.assertEqual(matched, ("pressure-write",))
        exception = b"\x00\x09\x00\x00\x00\x03\x01\x86\x02"
        self.assertEqual(relay.forward_response(exception), (exception, ()))
        self.assertEqual(relay.pending, {})

    def test_correlator_rewrites_fc16_and_validates_server_echo(self) -> None:
        relay = ModbusTransactionCorrelator([
            RewriteRule("middle", 1, 16, 11, 65000),
        ])
        original = _multiple_write_request(values=(42, 43, 44))
        forwarded, matched = relay.forward_request_with_matches(original)
        self.assertEqual(matched, ("middle",))
        self.assertEqual([int.from_bytes(forwarded[offset:offset + 2], "big")
                          for offset in (13, 15, 17)], [42, 65000, 44])
        echo = forwarded[:4] + b"\x00\x06" + forwarded[6:12]
        self.assertEqual(relay.forward_response(echo), (echo, ()))
        self.assertEqual(relay.pending, {})


if __name__ == "__main__":
    unittest.main()
