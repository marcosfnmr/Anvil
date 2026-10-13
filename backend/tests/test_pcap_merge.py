from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.pcap_merge import merge_pcaps  # noqa: E402


def pcap(seconds: int, payload: bytes) -> bytes:
    return (b"\xd4\xc3\xb2\xa1" + struct.pack("<HHIIII", 2, 4, 0, 0, 128, 1)
            + struct.pack("<IIII", seconds, 0, len(payload), len(payload)) + payload)


class PcapMergeTests(unittest.TestCase):
    def test_merges_by_timestamp_and_removes_duplicate_frames(self) -> None:
        merged = merge_pcaps([pcap(2, b"b"), pcap(1, b"a"), pcap(2, b"b")])
        self.assertEqual(merged[:4], b"\xd4\xc3\xb2\xa1")
        first = struct.unpack_from("<IIII", merged, 24)
        second = struct.unpack_from("<IIII", merged, 41)
        self.assertEqual((first[0], merged[40:41]), (1, b"a"))
        self.assertEqual((second[0], merged[57:58]), (2, b"b"))
        self.assertEqual(len(merged), 58)

    def test_rejects_corrupt_or_over_limit_capture(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid classic PCAP"):
            merge_pcaps([b"not pcap"])
        with self.assertRaisesRegex(ValueError, "export limit"):
            merge_pcaps([pcap(1, b"payload")], max_bytes=24)

    def test_preserves_linux_cooked_link_type_from_tcpdump_any(self) -> None:
        source = bytearray(pcap(1, b"packet"))
        struct.pack_into("<I", source, 20, 276)
        merged = merge_pcaps([bytes(source)])
        self.assertEqual(struct.unpack_from("<I", merged, 20)[0], 276)
        with self.assertRaisesRegex(ValueError, "different link types"):
            merge_pcaps([bytes(source), pcap(2, b"ethernet")])


if __name__ == "__main__":
    unittest.main()
