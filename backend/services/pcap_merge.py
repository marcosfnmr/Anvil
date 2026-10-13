"""Bounded classic-PCAP merge for packet captures from managed endpoints."""

from __future__ import annotations

import struct


_MAGIC = {
    b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
    b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
    b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
    b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
}


def merge_pcaps(pcaps: list[bytes], *, max_bytes: int = 8 * 1024 * 1024) -> bytes:
    """Merge classic PCAP streams of one link type by timestamp."""
    if not pcaps:
        raise ValueError("No packet captures were supplied")
    packets: list[tuple[int, int, int, bytes]] = []
    link_type: int | None = None
    snaplen = 0
    for pcap in pcaps:
        if len(pcap) < 24 or pcap[:4] not in _MAGIC:
            raise ValueError("Capture output is not a valid classic PCAP file")
        endian, units = _MAGIC[pcap[:4]]
        major, minor, _zone, _sigfigs, current_snaplen, current_link = struct.unpack_from(endian + "HHIIII", pcap, 4)
        if (major, minor) != (2, 4) or current_link <= 0 or not 1 <= current_snaplen <= 65535:
            raise ValueError("Only classic PCAP 2.4 captures with a valid link type can be merged")
        if link_type is not None and current_link != link_type:
            raise ValueError("Capture files use different link types and cannot be merged")
        link_type, snaplen = current_link, max(snaplen, current_snaplen)
        offset = 24
        while offset < len(pcap):
            if offset + 16 > len(pcap):
                raise ValueError("Capture contains a truncated packet header")
            seconds, fraction, included, original = struct.unpack_from(endian + "IIII", pcap, offset)
            offset += 16
            if fraction >= units or included > current_snaplen or included > original or offset + included > len(pcap):
                raise ValueError("Capture contains an invalid or truncated packet")
            payload = pcap[offset:offset + included]
            offset += included
            micros = fraction if units == 1_000_000 else fraction // 1000
            packets.append((seconds, micros, original, payload))
    packets.sort(key=lambda packet: (packet[0], packet[1]))
    header = b"\xd4\xc3\xb2\xa1" + struct.pack("<HHIIII", 2, 4, 0, 0, snaplen, link_type or 1)
    output = bytearray(header)
    seen: set[tuple[int, int, int, bytes]] = set()
    for seconds, micros, original, payload in packets:
        packet = (seconds, micros, original, payload)
        if packet in seen:
            continue
        seen.add(packet)
        output.extend(struct.pack("<IIII", seconds, micros, len(payload), original))
        output.extend(payload)
        if len(output) > max_bytes:
            raise ValueError(f"Merged capture exceeds the {max_bytes // (1024 * 1024)} MiB export limit")
    return bytes(output)
