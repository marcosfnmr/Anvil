"""Start the real Pymodbus server and verify TCP reads/writes on loopback."""

from __future__ import annotations

import os
import random
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException

ROOT = Path(__file__).resolve().parents[1]
PLC_DIR = Path(os.environ.get("ANVIL_PLC_DIR", ROOT / "plc"))
sys.path.insert(0, str(PLC_DIR))
from modbus_schema import ModbusConfig  # noqa: E402
from protocol_audit import ProtocolAuditQueue  # noqa: E402


def wait_for_server(process: subprocess.Popen, port: int, label: str) -> None:
    # Importing the pinned protocol stack from a Windows-mounted checkout can
    # take several seconds under WSL; allow cold starts without masking exits.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    _stdout, stderr = process.communicate()
    detail = stderr.decode("utf-8", errors="replace").strip() if isinstance(stderr, bytes) else (stderr or "").strip()
    suffix = f"; server output: {detail}" if detail else f"; server exited without diagnostics (code {process.returncode})"
    raise AssertionError(f"{label} did not start{suffix}")


def run_profile(profile: str, response_delay_ms: int = 0, response_jitter_ms: int = 0,
                response_jitter_distribution: str = "deterministic", response_jitter_seed: int = 0,
                fault_probability: float = 1.0, fault_seed: int = 0,
                verify_semantic_trace: bool = False) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = ModbusConfig.model_validate({
        "unit_id": 5,
        "port": port,
        "points": [
            {"type": "coil", "name": "pump", "address": 0, "initial_value": False},
            {"type": "coil", "name": "pump-aux", "address": 1, "initial_value": False},
            {"type": "coil", "name": "last-coil", "address": 65535, "initial_value": True},
            {"type": "discrete_input", "name": "door", "address": 2, "initial_value": True},
            {"type": "discrete_input", "name": "last-input-bit", "address": 65535, "initial_value": False},
            {"type": "holding_register", "name": "speed", "address": 7, "initial_value": 120},
            {"type": "holding_register", "name": "speed-aux", "address": 8, "initial_value": 0},
            {"type": "holding_register", "name": "last-holding", "address": 65535, "initial_value": 65535},
            {"type": "input_register", "name": "pressure", "address": 3, "initial_value": 42},
            {"type": "input_register", "name": "last-input", "address": 65535, "initial_value": 65535},
        ],
        "response_delay_ms": response_delay_ms,
        "response_jitter_ms": response_jitter_ms,
        "response_jitter_distribution": response_jitter_distribution,
        "response_jitter_seed": response_jitter_seed,
        "fault_mode": "device_failure" if profile == "probabilistic" else profile,
        "fault_probability": fault_probability,
        "fault_seed": fault_seed,
    })
    env = {**os.environ, "MODBUS_CONFIG": config.model_dump_json()}
    audit_directory = tempfile.TemporaryDirectory(prefix="anvil-modbus-audit-")
    audit_path = str(Path(audit_directory.name) / "events.sqlite3")
    env["ANVIL_PROTOCOL_AUDIT_PATH"] = audit_path
    process = subprocess.Popen(
        [sys.executable, "modbus_server.py"],
        cwd=PLC_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    client = ModbusTcpClient("127.0.0.1", port=port, timeout=2)
    try:
        wait_for_server(process, port, "Modbus TCP server")
        client.connect()
        if not client.connected:
            raise AssertionError("Modbus TCP server did not start")
        started = time.monotonic()
        try:
            response = client.read_coils(0, count=1, device_id=5)
        except ConnectionException:
            if profile != "disconnect":
                raise
            response = None
        elapsed = time.monotonic() - started
        if profile == "device_failure":
            assert response.isError(), response
            assert response.exception_code == 4, response
            print("TCP behavior OK: Function Code 1 receives Device Failure exception (code 4)")
        elif profile == "probabilistic":
            responses = [response] + [client.read_coils(0, count=1, device_id=5) for _ in range(19)]
            expected_rng = random.Random(fault_seed)
            expected_faults = [expected_rng.random() < fault_probability for _ in responses]
            actual_faults = [result.isError() for result in responses]
            assert actual_faults == expected_faults, (actual_faults, expected_faults)
            assert all(result.exception_code == 4 for result, fault in zip(responses, expected_faults) if fault)
            assert all(result.bits[0] is False for result, fault in zip(responses, expected_faults) if not fault)
            print(f"TCP behavior OK: seeded Device Failure probability {fault_probability} reproduced over {len(responses)} requests")
        elif profile == "disconnect":
            assert response is None or response.isError(), response
            print("TCP behavior OK: configured fault closes the client connection without a response")
        else:
            assert response is not None and not response.isError(), response
            assert response.bits[0] is False
            assert elapsed >= response_delay_ms / 1000 - 0.03, (elapsed, response_delay_ms)
            assert elapsed <= (response_delay_ms + response_jitter_ms) / 1000 + 0.15, (
                elapsed, response_delay_ms, response_jitter_ms,
            )
            assert client.read_holding_registers(7, count=1, device_id=5).registers == [120]
            assert client.read_input_registers(3, count=1, device_id=5).registers == [42]
            assert client.read_discrete_inputs(2, count=1, device_id=5).bits[0] is True
            assert client.read_discrete_inputs(65535, count=1, device_id=5).bits[0] is False
            assert client.read_coils(65535, count=1, device_id=5).bits[0] is True
            assert client.read_holding_registers(65535, count=1, device_id=5).registers == [65535]
            assert client.read_input_registers(65535, count=1, device_id=5).registers == [65535]
            for response in (
                client.read_coils(2, count=1, device_id=5),
                client.read_holding_registers(9, count=1, device_id=5),
                client.read_input_registers(4, count=1, device_id=5),
                client.read_discrete_inputs(3, count=1, device_id=5),
                client.read_holding_registers(65535, count=2, device_id=5),
            ):
                assert response.isError(), response
                assert response.exception_code == 2, response
            assert not client.write_coil(0, True, device_id=5).isError()
            assert not client.write_register(7, 1500, device_id=5).isError()
            assert client.read_coils(0, count=1, device_id=5).bits[0] is True
            assert client.read_holding_registers(7, count=1, device_id=5).registers == [1500]
            # Exercise standard FC15 (Write Multiple Coils) and FC16 (Write
            # Multiple Holding Registers), then read the same ranges back.
            response = client.write_coils(0, [False, True], device_id=5)
            assert not response.isError(), response
            assert response.function_code == 15, response
            response = client.write_registers(7, [210, 211], device_id=5)
            assert not response.isError(), response
            assert response.function_code == 16, response
            coils = client.read_coils(0, count=2, device_id=5)
            registers = client.read_holding_registers(7, count=2, device_id=5)
            assert not coils.isError() and coils.bits[:2] == [False, True], coils
            assert not registers.isError() and registers.registers == [210, 211], registers
            # FC22 applies bit masks to a single holding register; FC23 writes
            # multiple registers and returns the requested read range.
            response = client.mask_write_register(address=7, and_mask=0xFF00, or_mask=0x0005, device_id=5)
            assert not response.isError(), response
            assert response.function_code == 22, response
            assert client.read_holding_registers(7, count=1, device_id=5).registers == [5]
            response = client.readwrite_registers(
                read_address=7, read_count=2, write_address=7, values=[305, 306], device_id=5,
            )
            assert not response.isError(), response
            assert response.function_code == 23, response
            assert response.registers == [305, 306], response
            print(f"TCP integration OK: FC01-06, FC15/16, FC22 and FC23; "
                  f"{elapsed * 1000:.0f} ms observed, fixed={response_delay_ms} ms, jitter_max={response_jitter_ms} ms")
    finally:
        client.close()
        process.terminate()
        try:
            _stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            _stdout, stderr = process.communicate()
        if verify_semantic_trace:
            trace = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr
            assert "operation=read_coils" in trace and "space=coil address=0 count=1" in trace, trace
            assert "source=127.0.0.1" in trace, trace
            assert "operation=write_holding_register" in trace and "count=1 result=ok" in trace, trace
            assert "1500" not in trace, "semantic trace must not contain point values"
            durable_queue = ProtocolAuditQueue(audit_path)
            try:
                pending = durable_queue.pending()
                assert pending["events"], "protocol writes must enter the durable queue"
                audit_lines = [item["line"] for item in pending["events"]]
                assert any("operation=write_coil " in line for line in audit_lines), audit_lines
                assert any("operation=write_holding_register " in line for line in audit_lines), audit_lines
                assert any("operation=read_write_registers " in line for line in audit_lines), audit_lines
                assert any("result=exception code=" in line for line in audit_lines), audit_lines
                assert not any("operation=read_coils " in line and "result=ok" in line
                               for line in audit_lines), "successful reads must not be audited"
                assert all("value=" not in item["line"] and "data=" not in item["line"]
                           for item in pending["events"]), "durable audit queue must not contain value fields"
                assert pending["pending_count"] == len(pending["events"]), pending
            finally:
                durable_queue.close()
                audit_directory.cleanup()
        else:
            audit_directory.cleanup()


def main() -> int:
    run_profile("none", response_delay_ms=150, response_jitter_ms=50, verify_semantic_trace=True)
    for distribution in ("uniform", "normal"):
        # Exercise a real Modbus TCP server and standard PyModbus client over
        # enough requests to observe the configured statistical profile.
        run_statistical_profile(distribution)
    run_profile("device_failure")
    run_profile("disconnect")
    run_profile("probabilistic", fault_probability=0.45, fault_seed=2026)
    return 0


def run_statistical_profile(distribution: str) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = ModbusConfig.model_validate({
        "unit_id": 5, "port": port, "response_delay_ms": 20, "response_jitter_ms": 60,
        "response_jitter_distribution": distribution, "response_jitter_seed": 2026,
        "points": [{"type": "coil", "name": "pump", "address": 0}],
    })
    audit_directory = tempfile.TemporaryDirectory(prefix="anvil-modbus-audit-")
    process = subprocess.Popen([sys.executable, "modbus_server.py"], cwd=PLC_DIR,
        env={**os.environ, "MODBUS_CONFIG": config.model_dump_json(),
             "ANVIL_PROTOCOL_AUDIT_PATH": str(Path(audit_directory.name) / "events.sqlite3")},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    client = ModbusTcpClient("127.0.0.1", port=port, timeout=2)
    try:
        wait_for_server(process, port, "Modbus TCP statistical profile server")
        client.connect()
        delays = []
        for _ in range(40):
            started = time.monotonic()
            response = client.read_coils(0, count=1, device_id=5)
            elapsed_ms = (time.monotonic() - started) * 1000
            assert response is not None and not response.isError(), response
            assert 20 <= elapsed_ms <= 280, elapsed_ms
            delays.append(elapsed_ms - 20)
        mean = sum(delays) / len(delays)
        assert 18 <= mean <= 48, (distribution, mean)
        assert max(delays) - min(delays) >= 8, (distribution, min(delays), max(delays))
        print(f"TCP jitter integration OK: profile={distribution}, seed=2026, "
              f"mean={mean:.1f} ms, observed range={min(delays):.1f}–{max(delays):.1f} ms")
    finally:
        client.close()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        audit_directory.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
