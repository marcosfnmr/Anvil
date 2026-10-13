"""Read and write Modbus points from a client container on an Anvil network."""

from __future__ import annotations

import argparse
import sys

from pymodbus.client import ModbusTcpClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", required=True)
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--unit-id", type=int, default=1)
    parser.add_argument("--network", help="Docker network used by Anvil (optional; pass when launched with docker run)")
    args = parser.parse_args()
    client = ModbusTcpClient(args.ip, port=args.port, timeout=3)
    if not client.connect():
        raise SystemExit(f"Unable to connect to {args.ip}:{args.port}")
    try:
        coil = client.read_coils(0, count=1, device_id=args.unit_id)
        holding = client.read_holding_registers(0, count=1, device_id=args.unit_id)
        input_register = client.read_input_registers(0, count=1, device_id=args.unit_id)
        discrete_input = client.read_discrete_inputs(0, count=1, device_id=args.unit_id)
        if coil.isError() or holding.isError() or input_register.isError():
            raise AssertionError("Initial Modbus reads failed")
        old_coil, old_register = coil.bits[0], holding.registers[0]
        expected_coil, expected_register = not old_coil, (old_register + 1) % 65536
        if client.write_coil(0, expected_coil, device_id=args.unit_id).isError():
            raise AssertionError("Coil write failed")
        if client.write_register(0, expected_register, device_id=args.unit_id).isError():
            raise AssertionError("Holding register write failed")
        coil_after = client.read_coils(0, count=1, device_id=args.unit_id)
        register_after = client.read_holding_registers(0, count=1, device_id=args.unit_id)
        if coil_after.isError() or coil_after.bits[0] != expected_coil:
            raise AssertionError("Coil write did not persist")
        if register_after.isError() or register_after.registers[0] != expected_register:
            raise AssertionError("Holding register write did not persist")
        discrete_text = f"; discrete input 0: {discrete_input.bits[0]}" if not discrete_input.isError() else ""
        print(f"coil 0: {old_coil} -> {coil_after.bits[0]}{discrete_text}; holding register 0: {old_register} -> {register_after.registers[0]}; input register 0: {input_register.registers[0]}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
