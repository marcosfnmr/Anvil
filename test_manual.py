"""Read the example PLC's valve and analog sensor once per second."""

from __future__ import annotations

import argparse
import struct
import sys
import time

from snap7.client import Client


DB_NUMBER = 1
VALVE_BYTE_OFFSET = 0
VALVE_BIT_OFFSET = 0
PRESSURE_BYTE_OFFSET = 2
PRESSURE_UNIT = "bar"
READ_SIZE = PRESSURE_BYTE_OFFSET + 4


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="PLC host address")
    parser.add_argument("--port", type=int, default=102, help="PLC TCP port")
    parser.add_argument("--rack", type=int, default=0, help="S7 rack number")
    parser.add_argument("--slot", type=int, default=1, help="S7 slot number")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    client = Client()
    start_time = time.monotonic()

    try:
        client.connect(args.host, args.rack, args.slot, tcp_port=args.port)
        print(f"Connected to S7 PLC at {args.host}:{args.port}. Press Ctrl+C to stop.")

        while True:
            db_data = client.db_read(DB_NUMBER, 0, READ_SIZE)
            valve = bool(db_data[VALVE_BYTE_OFFSET] & (1 << VALVE_BIT_OFFSET))
            pressure = struct.unpack_from(">f", db_data, PRESSURE_BYTE_OFFSET)[0]
            elapsed = time.monotonic() - start_time
            print(
                f"[t={elapsed:.1f}s] valve={valve} "
                f"presion={pressure:.2f} {PRESSURE_UNIT}"
            )
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping manual test.")
        return 0
    except Exception as error:
        print(f"Unable to read the PLC: {error}", file=sys.stderr)
        return 1
    finally:
        if client.get_connected():
            client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
