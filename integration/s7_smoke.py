"""Protocol-level smoke test for a running Anvil S7 simulator."""

from __future__ import annotations

import argparse
import math
import struct
import sys
import time

from snap7.client import Client


DB_NUMBER = 1
VALVE_BYTE_OFFSET = 0
VALVE_BIT_OFFSET = 0
SENSOR_BYTE_OFFSET = 2
READ_SIZE = SENSOR_BYTE_OFFSET + 4


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1102)
    parser.add_argument("--rack", type=int, default=0)
    parser.add_argument("--slot", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def connect_with_retry(client: Client, args: argparse.Namespace) -> None:
    deadline = time.monotonic() + args.timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            client.connect(
                args.host,
                args.rack,
                args.slot,
                tcp_port=args.port,
            )
            if client.get_connected():
                return
        except Exception as error:
            last_error = error
        time.sleep(0.5)
    raise RuntimeError(f"S7 server did not become ready: {last_error}")


def main() -> int:
    args = parse_arguments()
    client = Client()

    try:
        connect_with_retry(client, args)
        initial = client.db_read(DB_NUMBER, 0, READ_SIZE)
        sensor_value = struct.unpack_from(">f", initial, SENSOR_BYTE_OFFSET)[0]
        if not math.isfinite(sensor_value):
            raise AssertionError(f"sensor value is not finite: {sensor_value}")

        original_byte = initial[VALVE_BYTE_OFFSET]
        mask = 1 << VALVE_BIT_OFFSET
        expected_state = not bool(original_byte & mask)
        changed_byte = original_byte ^ mask
        client.db_write(DB_NUMBER, VALVE_BYTE_OFFSET, bytearray([changed_byte]))
        observed = client.db_read(DB_NUMBER, VALVE_BYTE_OFFSET, 1)
        observed_state = bool(observed[0] & mask)
        if observed_state != expected_state:
            raise AssertionError(
                f"valve write was not preserved: expected {expected_state}, "
                f"observed {observed_state}"
            )

        client.db_write(DB_NUMBER, VALVE_BYTE_OFFSET, bytearray([original_byte]))
        print(
            "S7 smoke test passed: "
            f"sensor={sensor_value:.3f}, valve_write={observed_state}"
        )
        return 0
    except Exception as error:
        print(f"S7 smoke test failed: {error}", file=sys.stderr)
        return 1
    finally:
        if client.get_connected():
            client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
