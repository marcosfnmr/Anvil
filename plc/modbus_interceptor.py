"""Bounded Modbus TCP framing and exact read/write rewrites for Anvil routes."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Iterable


MIN_MBAP_LENGTH = 2  # Unit ID + function code
MAX_MBAP_LENGTH = 254


class ModbusFrameError(ValueError):
    """Raised when an MBAP frame is malformed or exceeds protocol bounds."""


@dataclass(frozen=True)
class ReadRequest:
    transaction_id: int
    unit_id: int
    function_code: int
    start_address: int
    quantity: int
    rewritten_rule_ids: tuple[str, ...] = ()
    write_values: tuple[int, ...] = ()

    @property
    def is_write(self) -> bool:
        return self.function_code in {5, 6, 15, 16, 22}


@dataclass(frozen=True)
class RewriteRule:
    rule_id: str
    unit_id: int
    function_code: int
    address: int
    replacement_value: int
    exercise_id: str | None = None
    event_id: str | None = None
    phase: str = "read"


class ModbusTransactionCorrelator:
    """Correlate read requests and responses without assuming TCP packet boundaries.

    Call :meth:`forward_request` for each complete client ADU and
    :meth:`forward_response` for each complete server ADU. FC01/FC02/FC03/FC04/FC23 responses
    and coil/register FC05/FC06/FC15/FC16/FC22 requests can be rewritten exactly;
    other functions remain byte-transparent. A bounded pending table prevents a
    client from growing memory without limit.
    """

    def __init__(self, rules: Iterable[RewriteRule] = (), *, max_pending: int = 256):
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be between 1 and 4096")
        self.rules = tuple(rules)
        self.max_pending = max_pending
        self.pending: dict[int, ReadRequest] = {}
        self._lock = Lock()

    def replace_rules(self, rules: Iterable[RewriteRule]) -> None:
        validated = tuple(rules)
        # Validate rules before publishing them to concurrent relay threads.
        for rule in validated:
            _validate_rule(rule)
        with self._lock:
            self.rules = validated

    def forward_request(self, frame: bytes) -> bytes:
        return self.forward_request_with_matches(frame)[0]

    def forward_request_with_matches(self, frame: bytes) -> tuple[bytes, tuple[str, ...]]:
        _validate_complete_frame(frame)
        request = decode_read_request(frame)
        if request is None:
            return frame, ()
        if request.function_code == 23:
            forwarded, matched = rewrite_write_request(frame, request, self.rules)
        elif request.function_code in {5, 6, 15, 16, 22}:
            forwarded, matched = rewrite_write_request(frame, request, self.rules)
            if request.function_code == 15:
                values = tuple((forwarded[13 + offset // 8] >> (offset % 8)) & 1
                               for offset in range(request.quantity))
            elif request.function_code == 5:
                values = (int.from_bytes(forwarded[10:12], "big") == 0xFF00,)
            elif request.function_code == 22:
                values = (int.from_bytes(forwarded[10:12], "big"),
                          int.from_bytes(forwarded[12:14], "big"))
            else:
                values_offset = 10 if request.function_code == 6 else 13
                values = tuple(int.from_bytes(forwarded[offset:offset + 2], "big")
                               for offset in range(values_offset, len(forwarded), 2))
            request = ReadRequest(
                transaction_id=request.transaction_id, unit_id=request.unit_id,
                function_code=request.function_code, start_address=request.start_address,
                quantity=request.quantity, rewritten_rule_ids=matched,
                write_values=values,
            )
        else:
            forwarded, matched = frame, ()
        transaction_id = request.transaction_id
        with self._lock:
            if transaction_id in self.pending:
                raise ModbusFrameError("a Modbus transaction ID is already awaiting a response")
            if len(self.pending) >= self.max_pending:
                raise ModbusFrameError("too many outstanding Modbus transactions")
            self.pending[transaction_id] = request
        return forwarded, matched

    def forward_response(self, frame: bytes) -> tuple[bytes, tuple[str, ...]]:
        _validate_complete_frame(frame)
        transaction_id = int.from_bytes(frame[0:2], "big")
        with self._lock:
            request = self.pending.get(transaction_id)
            if request is None:
                return frame, ()
            if request.is_write:
                if (frame[6] != request.unit_id
                        or frame[7] not in {request.function_code, request.function_code | 0x80}):
                    raise ModbusFrameError("Modbus write response does not match its request")
                if request.function_code == 5:
                    encoded_value = 0xFF00 if request.write_values[0] else 0
                    expected_echo = request.start_address.to_bytes(2, "big") + encoded_value.to_bytes(2, "big")
                elif request.function_code == 22:
                    expected_echo = (request.start_address.to_bytes(2, "big")
                                     + request.write_values[0].to_bytes(2, "big")
                                     + request.write_values[1].to_bytes(2, "big"))
                else:
                    expected_echo = (request.start_address.to_bytes(2, "big")
                                     + (request.write_values[0].to_bytes(2, "big")
                                        if request.function_code == 6
                                        else request.quantity.to_bytes(2, "big")))
                if frame[7] == request.function_code and (
                        len(frame) != 8 + len(expected_echo) or frame[8:] != expected_echo):
                    raise ModbusFrameError("Modbus write response echo does not match its request")
                if frame[7] & 0x80 and len(frame) != 9:
                    raise ModbusFrameError("Modbus write exception response has an invalid length")
                self.pending.pop(transaction_id, None)
                return frame, ()
            rewritten, matched = rewrite_read_response(frame, request, self.rules)
            # Consume only after validating the response. If validation fails the
            # caller should close the TCP flow; retaining state also prevents a
            # retry on the same stream from becoming ambiguous.
            self.pending.pop(transaction_id, None)
            return rewritten, matched


def split_mbap_frame(buffer: bytes | bytearray) -> tuple[bytes, bytes] | None:
    """Return (first complete ADU, trailing bytes), or None for an incomplete ADU.

    Invalid protocol IDs and lengths fail closed instead of attempting to scan
    arbitrary stream bytes for a plausible frame boundary.
    """
    if len(buffer) < 6:
        return None
    protocol_id = int.from_bytes(buffer[2:4], "big")
    length = int.from_bytes(buffer[4:6], "big")
    if protocol_id != 0:
        raise ModbusFrameError("Modbus TCP protocol identifier must be zero")
    if not MIN_MBAP_LENGTH <= length <= MAX_MBAP_LENGTH:
        raise ModbusFrameError("Modbus TCP MBAP length is outside the supported range")
    frame_length = 6 + length
    if len(buffer) < frame_length:
        return None
    return bytes(buffer[:frame_length]), bytes(buffer[frame_length:])


def decode_read_request(frame: bytes) -> ReadRequest | None:
    """Decode supported Modbus bit/register reads and writes."""
    _validate_complete_frame(frame)
    function_code = frame[7]
    if function_code not in {1, 2, 3, 4, 5, 6, 15, 16, 22, 23}:
        return None
    start = int.from_bytes(frame[8:10], "big")
    if function_code == 23:
        if len(frame) < 17:
            raise ModbusFrameError("FC23 request is missing its write payload")
        read_quantity = int.from_bytes(frame[10:12], "big")
        write_start = int.from_bytes(frame[12:14], "big")
        write_quantity = int.from_bytes(frame[14:16], "big")
        byte_count = frame[16]
        if (not 1 <= read_quantity <= 125 or not 1 <= write_quantity <= 121
                or byte_count != write_quantity * 2 or len(frame) != 17 + byte_count):
            raise ModbusFrameError("FC23 read/write quantities or byte count are invalid")
        if start + read_quantity > 65_536 or write_start + write_quantity > 65_536:
            raise ModbusFrameError("FC23 read/write range is invalid")
        quantity = read_quantity
    elif function_code == 22:
        if len(frame) != 14:
            raise ModbusFrameError("FC22 request must be exactly 14 bytes")
        quantity = 1
    elif function_code in {15, 16}:
        minimum_length = 14 if function_code == 15 else 15
        if len(frame) < minimum_length:
            raise ModbusFrameError(f"FC{function_code} request is missing its value payload")
        quantity = int.from_bytes(frame[10:12], "big")
        byte_count = frame[12]
        max_quantity = 1968 if function_code == 15 else 123
        expected_byte_count = (quantity + 7) // 8 if function_code == 15 else quantity * 2
        if (not 1 <= quantity <= max_quantity or byte_count != expected_byte_count
                or len(frame) != 13 + byte_count):
            raise ModbusFrameError(f"FC{function_code} request quantity or byte count is invalid")
    elif function_code in {1, 2, 3, 4, 5, 6}:
        if len(frame) != 12:
            raise ModbusFrameError("FC03/FC04/FC05/FC06 request must be exactly 12 bytes")
        quantity = 1 if function_code in {5, 6} else int.from_bytes(frame[10:12], "big")
        if function_code == 5 and int.from_bytes(frame[10:12], "big") not in {0, 0xFF00}:
            raise ModbusFrameError("FC05 coil value must be 0x0000 or 0xFF00")
        max_quantity = 2000 if function_code in {1, 2} else 125
        if (function_code != 6 and not 1 <= quantity <= max_quantity):
            raise ModbusFrameError(f"FC{function_code} request quantity is invalid")
    if start + quantity > 65_536:
        raise ModbusFrameError("Modbus request range is invalid")
    return ReadRequest(
        transaction_id=int.from_bytes(frame[0:2], "big"),
        unit_id=frame[6],
        function_code=function_code,
        start_address=start,
        quantity=quantity,
        write_values=(tuple(int.from_bytes(frame[offset:offset + 2], "big")
                            for offset in range(13, len(frame), 2))
                      if function_code == 16 else
                      tuple((frame[13 + offset // 8] >> (offset % 8)) & 1
                            for offset in range(quantity)) if function_code == 15 else
                      (int.from_bytes(frame[10:12], "big"),) if function_code == 6 else
                      (int.from_bytes(frame[10:12], "big") == 0xFF00,) if function_code == 5 else
                      (int.from_bytes(frame[10:12], "big"), int.from_bytes(frame[12:14], "big"))
                      if function_code == 22 else ()),
    )


def rewrite_single_write_request(
    request_frame: bytes,
    request: ReadRequest,
    rules: Iterable[RewriteRule],
) -> tuple[bytes, tuple[str, ...]]:
    """Replace the value of an exact FC06 write while preserving its address."""
    if request.function_code != 6:
        return request_frame, ()
    applicable = [rule for rule in rules
                  if rule.unit_id == request.unit_id and rule.function_code == 6
                  and rule.address == request.start_address]
    if len(applicable) > 1:
        raise ValueError("multiple rewrite rules target the same FC06 register")
    if not applicable:
        return request_frame, ()
    rule = applicable[0]
    _validate_rule(rule)
    rewritten = bytearray(request_frame)
    rewritten[10:12] = rule.replacement_value.to_bytes(2, "big")
    return bytes(rewritten), (rule.rule_id,)


def rewrite_write_request(
    request_frame: bytes,
    request: ReadRequest,
    rules: Iterable[RewriteRule],
) -> tuple[bytes, tuple[str, ...]]:
    """Rewrite exact coil/register values in supported Modbus write requests."""
    if request.function_code == 23:
        write_start = int.from_bytes(request_frame[12:14], "big")
        write_quantity = int.from_bytes(request_frame[14:16], "big")
        applicable: dict[int, RewriteRule] = {}
        for rule in rules:
            _validate_rule(rule)
            if (rule.unit_id == request.unit_id and rule.function_code == 23
                    and rule.phase == "write"
                    and write_start <= rule.address < write_start + write_quantity):
                if rule.address in applicable:
                    raise ValueError("multiple rewrite rules target the same FC23 write register")
                applicable[rule.address] = rule
        if not applicable:
            return request_frame, ()
        rewritten = bytearray(request_frame)
        for address, rule in applicable.items():
            offset = 17 + (address - write_start) * 2
            rewritten[offset:offset + 2] = rule.replacement_value.to_bytes(2, "big")
        return bytes(rewritten), tuple(sorted(rule.rule_id for rule in applicable.values()))
    if request.function_code == 5:
        applicable = [rule for rule in rules
                      if rule.unit_id == request.unit_id and rule.function_code == 5
                      and rule.address == request.start_address]
        if len(applicable) > 1:
            raise ValueError("multiple rewrite rules target the same FC05 coil")
        if not applicable:
            return request_frame, ()
        rule = applicable[0]
        _validate_rule(rule)
        rewritten = bytearray(request_frame)
        rewritten[10:12] = (0xFF00 if rule.replacement_value else 0).to_bytes(2, "big")
        return bytes(rewritten), (rule.rule_id,)
    if request.function_code == 6:
        return rewrite_single_write_request(request_frame, request, rules)
    if request.function_code == 22:
        applicable = [rule for rule in rules
                      if rule.unit_id == request.unit_id and rule.function_code == 22
                      and rule.address == request.start_address]
        if len(applicable) > 1:
            raise ValueError("multiple rewrite rules target the same FC22 register")
        if not applicable:
            return request_frame, ()
        rule = applicable[0]
        _validate_rule(rule)
        # AND=0 clears the old value; OR=replacement forces the requested word.
        rewritten = bytearray(request_frame)
        rewritten[10:12] = b"\x00\x00"
        rewritten[12:14] = rule.replacement_value.to_bytes(2, "big")
        return bytes(rewritten), (rule.rule_id,)
    if request.function_code == 15:
        applicable: dict[int, RewriteRule] = {}
        for rule in rules:
            _validate_rule(rule)
            if (rule.unit_id == request.unit_id and rule.function_code == 15
                    and request.start_address <= rule.address < request.start_address + request.quantity):
                if rule.address in applicable:
                    raise ValueError("multiple rewrite rules target the same FC15 coil")
                applicable[rule.address] = rule
        if not applicable:
            return request_frame, ()
        rewritten = bytearray(request_frame)
        matched: list[str] = []
        for address, rule in applicable.items():
            bit_index = address - request.start_address
            byte_offset = 13 + bit_index // 8
            mask = 1 << (bit_index % 8)
            if rule.replacement_value:
                rewritten[byte_offset] |= mask
            else:
                rewritten[byte_offset] &= ~mask
            matched.append(rule.rule_id)
        return bytes(rewritten), tuple(sorted(matched))
    if request.function_code != 16:
        return request_frame, ()

    applicable: dict[int, RewriteRule] = {}
    for rule in rules:
        _validate_rule(rule)
        if (rule.unit_id == request.unit_id and rule.function_code == 16
                and request.start_address <= rule.address < request.start_address + request.quantity):
            if rule.address in applicable:
                raise ValueError("multiple rewrite rules target the same FC16 register")
            applicable[rule.address] = rule
    if not applicable:
        return request_frame, ()

    rewritten = bytearray(request_frame)
    matched: list[str] = []
    for address, rule in applicable.items():
        offset = 13 + (address - request.start_address) * 2
        rewritten[offset:offset + 2] = rule.replacement_value.to_bytes(2, "big")
        matched.append(rule.rule_id)
    return bytes(rewritten), tuple(sorted(matched))


def rewrite_read_response(
    response: bytes,
    request: ReadRequest,
    rules: Iterable[RewriteRule],
) -> tuple[bytes, tuple[str, ...]]:
    """Replace exact bits/UINT16 values in a correlated Modbus read response.

    Frames with different transaction/unit/function identifiers and Modbus
    exception responses pass through unchanged. Malformed responses raise so
    a caller can close the flow rather than emit a corrupted or ambiguous ADU.
    """
    _validate_complete_frame(response)
    if (int.from_bytes(response[0:2], "big") != request.transaction_id
            or response[6] != request.unit_id
            or response[7] != request.function_code
            or response[7] & 0x80):
        return response, ()

    if len(response) < 9:
        raise ModbusFrameError(f"FC{request.function_code} response is missing its byte count")

    bit_read = request.function_code in {1, 2}
    expected_bytes = (request.quantity + 7) // 8 if bit_read else request.quantity * 2
    if (len(response) != 9 + response[8]
            or response[8] != expected_bytes):
        raise ModbusFrameError(f"FC{request.function_code} response length does not match its request")

    applicable: dict[int, RewriteRule] = {}
    for rule in rules:
        _validate_rule(rule)
        if (rule.unit_id == request.unit_id and rule.function_code == request.function_code
                and rule.phase == "read"
                and request.start_address <= rule.address < request.start_address + request.quantity):
            if rule.address in applicable:
                raise ValueError("multiple rewrite rules target the same point in one response")
            applicable[rule.address] = rule

    if not applicable:
        return response, ()
    rewritten = bytearray(response)
    matched: list[str] = []
    for address, rule in applicable.items():
        offset = address - request.start_address
        if bit_read:
            byte_index, bit_index = 9 + offset // 8, offset % 8
            mask = 1 << bit_index
            if rule.replacement_value:
                rewritten[byte_index] |= mask
            else:
                rewritten[byte_index] &= ~mask
        else:
            byte_index = 9 + offset * 2
            rewritten[byte_index:byte_index + 2] = rule.replacement_value.to_bytes(2, "big")
        matched.append(rule.rule_id)
    return bytes(rewritten), tuple(sorted(matched))


def _validate_complete_frame(frame: bytes) -> None:
    if len(frame) < 8:
        raise ModbusFrameError("Modbus TCP ADU is too short")
    if int.from_bytes(frame[2:4], "big") != 0:
        raise ModbusFrameError("Modbus TCP protocol identifier must be zero")
    length = int.from_bytes(frame[4:6], "big")
    if not MIN_MBAP_LENGTH <= length <= MAX_MBAP_LENGTH or len(frame) != 6 + length:
        raise ModbusFrameError("Modbus TCP ADU length does not match its MBAP header")


def _validate_rule(rule: RewriteRule) -> None:
    if not isinstance(rule, RewriteRule):
        raise TypeError("rewrite rules must be RewriteRule values")
    if (not rule.rule_id or len(rule.rule_id) > 64
            or type(rule.unit_id) is not int or not 0 <= rule.unit_id <= 255
            or type(rule.function_code) is not int or rule.function_code not in {1, 2, 3, 4, 5, 6, 15, 16, 22, 23}
            or type(rule.address) is not int or not 0 <= rule.address <= 65_535
            or type(rule.replacement_value) is not int or not 0 <= rule.replacement_value <= 65_535):
        raise ValueError("invalid Modbus rewrite rule")
    if rule.phase not in {"read", "write"} or (rule.function_code != 23 and rule.phase != "read"):
        raise ValueError("rewrite phase is only configurable for FC23")
    if rule.function_code in {1, 2, 5, 15} and rule.replacement_value not in {0, 1}:
        raise ValueError(f"FC{rule.function_code} bit replacement must be 0 or 1")
