"""Protocol-level smoke test for a running Anvil S7 simulator."""

from __future__ import annotations

import argparse
import math
import struct
import subprocess
import sys
import time
from snap7.type import Area

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
    parser.add_argument("--minimum-delay-ms", type=float, default=0.0)
    parser.add_argument("--container-name", help="also assert DB/M/I/Q semantic traces in docker logs")
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
        started = time.monotonic()
        initial = client.db_read(DB_NUMBER, 0, READ_SIZE)
        response_ms = (time.monotonic() - started) * 1000
        if response_ms + 10 < args.minimum_delay_ms:
            raise AssertionError(f"S7 response delay was {response_ms:.1f} ms; expected at least {args.minimum_delay_ms} ms")
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
        # Independent process-image areas: marker and output BOOL round-trip;
        # input area is readable and serves the configured field input image.
        marker_area = int.from_bytes(client.read_area(Area.MK, 0, 0, 1), "big")
        marker_written = marker_area ^ 0x04
        client.write_area(Area.MK, 0, 0, bytearray([marker_written]))
        marker_observed = int.from_bytes(client.read_area(Area.MK, 0, 0, 1), "big")
        if marker_observed != marker_written:
            raise AssertionError("S7 marker memory write was not preserved")
        output_raw = bytearray(client.read_area(Area.PA, 0, 0, 1))
        output_raw[0] ^= 0x08
        client.write_area(Area.PA, 0, 0, output_raw)
        if client.read_area(Area.PA, 0, 0, 1) != output_raw:
            raise AssertionError("S7 output process image write was not preserved")
        client.read_area(Area.PE, 0, 0, 1)
        if args.container_name:
            log_result = subprocess.run(["docker", "logs", args.container_name], capture_output=True,
                                        text=True, check=True)
            logs = log_result.stdout + log_result.stderr
            for area_label in ("DB1", "M", "I", "Q"):
                if f" {area_label} bytes[" not in logs:
                    raise AssertionError(f"semantic S7 trace for {area_label} is missing from Docker logs")
        print(
            "S7 smoke test passed: "
            f"sensor={sensor_value:.3f}, valve_write={observed_state}, response={response_ms:.1f} ms, M/I/Q process images"
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
