"""Deploy a PLC program block and verify its result through live Snap7 reads."""
from __future__ import annotations

import os
import time
import uuid
from ipaddress import ip_network

import docker

from models.scenario import ScenarioCreate
from services.docker_manager import DockerManager
from services.scenario_service import ScenarioService


def free_subnet(client):
    used = [entry["Subnet"] for network in client.networks.list()
            for entry in (network.attrs.get("IPAM") or {}).get("Config") or [] if entry.get("Subnet")]
    for third in range(150, 220):
        candidate = ip_network(f"10.246.{third}.0/24")
        if all(not candidate.overlaps(ip_network(item)) for item in used):
            return candidate
    raise RuntimeError("No free PLC logic test subnet in 10.246.150.0/24–10.246.219.0/24")


def run_detached_exec(container, command: list[str], timeout_seconds: float = 12) -> int:
    """Avoid waiting forever on Docker's multiplexed exec output stream."""
    handle = container.client.api.exec_create(container.id, command)
    container.client.api.exec_start(handle["Id"], detach=True)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = container.client.api.exec_inspect(handle["Id"])
        if not result["Running"]:
            return int(result["ExitCode"] or 0)
        time.sleep(0.1)
    raise TimeoutError("Docker exec did not finish before the integration-test timeout")


def main() -> None:
    image = os.environ.get("ANVIL_PLC_IMAGE")
    if not image:
        raise RuntimeError("Set ANVIL_PLC_IMAGE to a local S7 runtime image")
    client = docker.from_env()
    manager = DockerManager(client=client)
    subnet = free_subnet(client)
    scenario_id = str(uuid.uuid4())
    payload = ScenarioCreate.model_validate({
        "name": "PLC function block smoke", "network": {"subnet": str(subnet)},
        "devices": [{
            "node_id": "plc", "device_type": "plc-s7", "name": "PLC control",
            "ip": str(subnet.network_address + 10),
            "config": {
                "components": [
                    {"type": "analog_sensor", "name": "Level", "min_value": 0, "max_value": 100},
                    {"type": "pump", "name": "Pump", "flow_min": 0, "flow_max": 100},
                    {"type": "digital_input", "name": "Start TON"},
                    {"type": "valve", "name": "Delayed valve"},
                    {"type": "digital_input", "name": "Start TOF"},
                    {"type": "valve", "name": "Delayed close valve"},
                    {"type": "digital_input", "name": "Permiso A"},
                    {"type": "digital_input", "name": "Permiso B"},
                    {"type": "valve", "name": "Interlocked valve"},
                    {"type": "digital_input", "name": "Arranque memoria"},
                    {"type": "digital_input", "name": "Parada memoria"},
                    {"type": "motor", "name": "Motor memorizado"},
                    {"type": "digital_input", "name": "Pulso contador"},
                    {"type": "digital_input", "name": "Reset contador"},
                    {"type": "valve", "name": "DONE contador"},
                    {"type": "digital_input", "name": "Avance secuencia"},
                    {"type": "digital_input", "name": "Reset secuencia"},
                    {"type": "valve", "name": "Paso uno"},
                    {"type": "valve", "name": "Paso dos"},
                    {"type": "valve", "name": "Paso tres"},
                ],
                "control_rules": [{"name": "Start below level", "input_component": "Level",
                                   "output_component": "Pump", "mode": "below", "setpoint": 60,
                                   "hysteresis": 5}],
                "timers": [{"name": "Delayed opening", "input_component": "Start TON",
                            "output_component": "Delayed valve", "preset_ms": 1500},
                           {"name": "Delayed closing", "input_component": "Start TOF",
                            "output_component": "Delayed close valve", "mode": "tof", "preset_ms": 600}],
                "boolean_rules": [{"name": "Two-hand interlock", "input_a_component": "Permiso A",
                                   "input_b_component": "Permiso B", "operator": "and",
                                   "input_b_negated": True,
                                   "output_component": "Interlocked valve"}],
                "latches": [{"name": "Motor SR", "set_component": "Arranque memoria",
                             "reset_component": "Parada memoria", "output_component": "Motor memorizado"}],
                "counters": [{"name": "CTU ciclos", "count_component": "Pulso contador",
                              "reset_component": "Reset contador", "output_component": "DONE contador", "preset": 2}],
                "sequences": [{"name": "Secuencia de transferencia", "advance_component": "Avance secuencia",
                               "reset_component": "Reset secuencia", "steps": ["Paso uno", "Paso dos", "Paso tres"]}],
            },
        }],
    })
    runtime = ScenarioService._build_runtime_devices(payload)
    try:
        result = manager.deploy(scenario_id, str(subnet), runtime)
        if result["status"] != "running":
            raise AssertionError(f"PLC did not become healthy: {result}")
        time.sleep(0.2)
        timer = runtime[0]["config"]["timers"][0]
        container = next(item for item in client.containers.list(
            filters={"label": f"anvil.scenario_id={scenario_id}"}
        ) if item.labels.get("anvil.node_id") == "plc")
        exercise_ton = f'''\
import snap7
import time
c = snap7.client.Client()
c.connect("127.0.0.1", 0, 1)
ib, im = {timer["input_byte_offset"]}, {1 << timer["input_bit_offset"]}
ob, om = {timer["output_byte_offset"]}, {1 << timer["output_bit_offset"]}
b = bytearray(c.db_read(1, ib, 1))
b[0] |= im
c.db_write(1, ib, b)
started = time.monotonic()
preset = {timer["preset_ms"]} / 1000
while time.monotonic() - started < preset:
    assert not (c.db_read(1, ob, 1)[0] & om), "TON output activated before preset"
    time.sleep(.025)
deadline = started + preset + 2
while time.monotonic() < deadline and not (c.db_read(1, ob, 1)[0] & om):
    time.sleep(.025)
assert c.db_read(1, ob, 1)[0] & om, "TON output did not activate after preset"
c.disconnect()
'''
        result = container.exec_run(["python", "-c", exercise_ton], stdout=True, stderr=True)
        if result.exit_code:
            raise RuntimeError(f"TON did not honor its preset through Snap7: {result.output!r}")
        reset_input = (
            "import snap7; c=snap7.client.Client(); c.connect('127.0.0.1', 0, 1); "
            f"b=bytearray(c.db_read(1, {timer['input_byte_offset']}, 1)); "
            f"b[0] &= ~{1 << timer['input_bit_offset']}; "
            f"c.db_write(1, {timer['input_byte_offset']}, b); c.disconnect()"
        )
        result = container.exec_run(["python", "-c", reset_input], stdout=True, stderr=True)
        if result.exit_code:
            raise RuntimeError(f"Unable to clear TON input through Snap7: {result.output!r}")
        time.sleep(0.15)
        reset_ton = (
            "import snap7; c=snap7.client.Client(); c.connect('127.0.0.1', 0, 1); "
            f"assert not (c.db_read(1, {timer['output_byte_offset']}, 1)[0] & {1 << timer['output_bit_offset']}); "
            "c.disconnect()"
        )
        result = container.exec_run(["python", "-c", reset_ton], stdout=True, stderr=True)
        if result.exit_code:
            raise RuntimeError(f"TON output did not reset through Snap7: {result.output!r}")
        tof = next(item for item in runtime[0]["config"]["timers"] if item["mode"] == "tof")
        exercise_tof = f'''\
import snap7, time
c=snap7.client.Client(); c.connect("127.0.0.1", 0, 1)
ib, im = {tof["input_byte_offset"]}, {1 << tof["input_bit_offset"]}
ob, om = {tof["output_byte_offset"]}, {1 << tof["output_bit_offset"]}
def read(): return bool(c.db_read(1, ob, 1)[0] & om)
def write(enabled):
    image=bytearray(c.db_read(1, ib, 1)); image[0]=image[0]|im if enabled else image[0]&~im
    c.db_write(1, ib, image)
write(True)
deadline=time.monotonic()+1
while time.monotonic()<deadline and not read(): time.sleep(.02)
assert read(), "TOF output did not follow the active input"
write(False); time.sleep(.15); assert read(), "TOF output dropped before the preset"
write(True); time.sleep(.1); write(False); time.sleep(.15)
assert read(), "TOF reassertion failed to restart the off-delay"
time.sleep(.55)
deadline=time.monotonic()+1
while time.monotonic()<deadline and read(): time.sleep(.02)
assert not read(), "TOF output did not drop after the preset"
c.disconnect()
'''
        result = container.exec_run(["python", "-c", exercise_tof], stdout=True, stderr=True)
        if result.exit_code:
            raise RuntimeError(f"TOF did not honor its preset through Snap7: {result.output!r}")
        boolean_rule = runtime[0]["config"]["boolean_logic"][0]
        exercise_interlock = f"""import snap7, time
c=snap7.client.Client(); c.connect('127.0.0.1', 0, 1)
a=({boolean_rule['input_a_byte_offset']}, {1 << boolean_rule['input_a_bit_offset']})
b=({boolean_rule['input_b_byte_offset']}, {1 << boolean_rule['input_b_bit_offset']})
out=({boolean_rule['output_byte_offset']}, {1 << boolean_rule['output_bit_offset']})
def setbit(offset, mask, enabled):
    image=bytearray(c.db_read(1, offset, 1))
    image[0]=image[0] | mask if enabled else image[0] & ~mask
    c.db_write(1, offset, image)
for a_on, b_on, expected in ((False,False,False),(True,False,True),(False,True,False),(True,True,False),(True,False,True)):
    setbit(*a, a_on); setbit(*b, b_on)
    deadline=time.monotonic()+1
    while time.monotonic()<deadline:
        actual=bool(c.db_read(1,out[0],1)[0] & out[1])
        if actual == expected: break
        time.sleep(.025)
    assert actual == expected, 'AND mismatch'
c.disconnect()
"""
        result = container.exec_run(["python", "-c", exercise_interlock], stdout=True, stderr=True)
        if result.exit_code:
            raise RuntimeError(f"BOOL AND interlock failed through Snap7: {result.output!r}")
        latch = runtime[0]["config"]["latches"][0]
        exercise_latch = "\n".join([
            "import snap7, time",
            "c=snap7.client.Client(); c.connect('127.0.0.1',0,1)",
            f"s=({latch['set_byte_offset']},{1 << latch['set_bit_offset']})",
            f"r=({latch['reset_byte_offset']},{1 << latch['reset_bit_offset']})",
            f"o=({latch['output_byte_offset']},{1 << latch['output_bit_offset']})",
            "def read(pair): return bool(c.db_read(1,pair[0],1)[0] & pair[1])",
            "def write(pair,on):\n b=bytearray(c.db_read(1,pair[0],1)); b[0]=b[0]|pair[1] if on else b[0]&~pair[1]; c.db_write(1,pair[0],b)",
            "write(s,True); write(r,False); time.sleep(.15); assert read(o), 'SR failed to set'",
            "write(s,False); time.sleep(.15); assert read(o), 'SR did not retain state'",
            "write(r,True); time.sleep(.15); assert not read(o), 'SR failed to reset'",
            "write(s,True); time.sleep(.15); assert not read(o), 'reset must dominate simultaneous inputs'",
            "write(r,False); time.sleep(.15); assert read(o), 'SR failed to set again'",
            "c.disconnect()",
        ])
        exit_code = run_detached_exec(container, ["timeout", "8s", "python", "-c", exercise_latch])
        if exit_code:
            raise RuntimeError(f"SR latch behavior failed through Snap7 (exit {exit_code})")
        counter = runtime[0]["config"]["counters"][0]
        exercise_counter = "\n".join([
            "import snap7, time",
            "c=snap7.client.Client(); c.connect('127.0.0.1',0,1)",
            f"pulse=({counter['count_byte_offset']},{1 << counter['count_bit_offset']})",
            f"reset=({counter['reset_byte_offset']},{1 << counter['reset_bit_offset']})",
            f"done=({counter['output_byte_offset']},{1 << counter['output_bit_offset']})",
            "def read(pair): return bool(c.db_read(1,pair[0],1)[0] & pair[1])",
            "def write(pair,on):\n b=bytearray(c.db_read(1,pair[0],1)); b[0]=b[0]|pair[1] if on else b[0]&~pair[1]; c.db_write(1,pair[0],b)",
            "write(pulse,True); time.sleep(.15); assert not read(done), 'DONE before preset'",
            "time.sleep(.15); assert not read(done), 'held input counted repeatedly'",
            "write(pulse,False); time.sleep(.15); write(pulse,True); time.sleep(.15); assert read(done), 'DONE did not reach preset'",
            "write(reset,True); time.sleep(.15); assert not read(done), 'RESET failed to clear count'",
            "write(pulse,False); write(reset,False); time.sleep(.15); write(pulse,True); write(reset,True); time.sleep(.15); assert not read(done), 'RESET must dominate count edge'",
            "c.disconnect()",
        ])
        exit_code = run_detached_exec(container, ["timeout", "8s", "python", "-c", exercise_counter])
        if exit_code:
            raise RuntimeError(f"CTU counter behavior failed through Snap7 (exit {exit_code})")
        sequence = runtime[0]["config"]["sequences"][0]
        exercise_sequence = "\n".join([
            "import snap7, time",
            "c=snap7.client.Client(); c.connect('127.0.0.1',0,1)",
            f"advance=({sequence['advance_byte_offset']},{1 << sequence['advance_bit_offset']})",
            f"reset=({sequence['reset_byte_offset']},{1 << sequence['reset_bit_offset']})",
            f"steps={[ (step['byte_offset'], 1 << step['bit_offset']) for step in sequence['steps'] ]!r}",
            "def read(pair): return bool(c.db_read(1,pair[0],1)[0] & pair[1])",
            "def write(pair,on):\n b=bytearray(c.db_read(1,pair[0],1)); b[0]=b[0]|pair[1] if on else b[0]&~pair[1]; c.db_write(1,pair[0],b)",
            "def active(index): return [i for i,step in enumerate(steps) if read(step)]",
            "time.sleep(.15); assert active(0)==[0], 'sequence must start at step 1'",
            "write(advance,True); time.sleep(.15); assert active(0)==[1], 'sequence failed to advance'",
            "time.sleep(.15); assert active(0)==[1], 'held input advanced more than once'",
            "write(advance,False); time.sleep(.15); write(advance,True); time.sleep(.15); assert active(0)==[2], 'sequence failed to reach terminal step'",
            "write(advance,False); time.sleep(.15); write(advance,True); time.sleep(.15); assert active(0)==[2], 'terminal step should hold'",
            "write(reset,True); time.sleep(.15); assert active(0)==[0], 'RESET failed to return to step 1'",
            "write(advance,False); write(reset,False); time.sleep(.15); write(advance,True); write(reset,True); time.sleep(.15); assert active(0)==[0], 'RESET must dominate advance'",
            "c.disconnect()",
        ])
        exit_code = run_detached_exec(container, ["timeout", "8s", "python", "-c", exercise_sequence])
        if exit_code:
            raise RuntimeError(f"PLC sequence behavior failed through Snap7 (exit {exit_code})")
        print("PASS: Snap7 REAL/BOOL inputs drive hysteresis, TON/TOF, NC, SR, CTU and one-hot sequence")
    finally:
        manager.stop(scenario_id)
        client.close()


if __name__ == "__main__":
    main()
