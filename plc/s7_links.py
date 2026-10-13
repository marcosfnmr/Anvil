"""Real S7 reads initiated inside the receiving PLC container."""
from __future__ import annotations

import json
import logging
import math
import os
import struct
import threading
import time
from datetime import datetime, timezone

from snap7.client import Client
from snap7.connection import ISOTCPConnection
from snap7.type import Parameter

LOGGER = logging.getLogger("anvil.plc.links")
STATUS_PATH = "/tmp/anvil-s7-links.json"


class TimedClient(Client):
    """Bound socket timeouts for the pinned python-snap7 3.2 runtime.

    Its normal connect() ignores the legacy timeout parameters. Isolate the
    connection setup here so a stalled peer cannot retain a worker indefinitely.
    """
    timeout_seconds = 1.0

    def _connect(self, address, rack, slot, tcp_port):
        self.host, self.port, self.rack, self.slot = address, tcp_port, rack, slot
        self.connection = ISOTCPConnection(host=address, port=tcp_port,
                                          local_tsap=self.local_tsap, remote_tsap=self.remote_tsap)
        try:
            self.connection.connect(timeout=self.timeout_seconds)
            self._setup_communication()
            self.connected = self._is_alive = True
        except Exception:
            self.disconnect()
            raise
        return self


class S7Links:
    def __init__(self, configs, buffers, stop_event, client_factory=TimedClient):
        self.configs = [item for item in configs if item.type == "remote_input"]
        self.buffers = buffers
        self.stop_event = stop_event
        self.client_factory = client_factory
        self.lock = threading.Lock()
        self.pending = {}
        self.status = {self.address(item): {
            "source_node_id": item.source_node_id, "source_address": item.source_address,
            "period_ms": item.period_ms, "stale_after_ms": item.stale_after_ms,
            "stale_behavior": item.stale_behavior,
            "last_success_at": None, "last_success_epoch": None, "error": None,
        } for item in self.configs}
        self.threads = []

    @staticmethod
    def address(config):
        return (f"DB{config.db}.DBX{config.byte_offset}.{config.bit_offset}" if config.data_type == "BOOL"
                else f"DB{config.db}.DBD{config.byte_offset}")

    def start(self):
        for config in self.configs:
            thread = threading.Thread(target=self.read_loop, args=(config,), daemon=True,
                                      name=f"s7-get-{self.address(config)}")
            self.threads.append(thread)
            thread.start()

    def read_loop(self, config):
        client = self.client_factory()
        if isinstance(client, TimedClient):
            client.timeout_seconds = config.timeout_ms / 1000
        key = self.address(config)
        failures = 0
        try:
            for parameter in (Parameter.PingTimeout, Parameter.SendTimeout, Parameter.RecvTimeout):
                client.set_param(parameter, config.timeout_ms)
            while not self.stop_event.is_set():
                started = time.monotonic()
                try:
                    if not client.get_connected():
                        client.connect(str(config.source_ip), config.source_rack, config.source_slot,
                                       tcp_port=config.source_port)
                    raw = bytes(client.db_read(config.source_db, config.source_byte_offset, 1 if config.data_type == "BOOL" else 4))
                    if config.data_type == "BOOL":
                        value = bool(raw[0] & (1 << config.source_bit_offset))
                    else:
                        value = struct.unpack(">f", raw)[0]
                        if not math.isfinite(value):
                            raise ValueError("Remote REAL is not finite")
                    with self.lock:
                        self.pending[key] = value
                        self.status[key].update(last_success_at=datetime.now(timezone.utc).isoformat(),
                                                last_success_epoch=time.time(), error=None)
                    if failures:
                        LOGGER.info("S7 GET recovered input=%s peer=%s", key, config.source_node_id)
                    failures = 0
                    delay = max(0, config.period_ms / 1000 - (time.monotonic() - started))
                except Exception as error:
                    with self.lock:
                        self.status[key]["error"] = str(error)
                    if not failures:
                        LOGGER.warning("S7 GET failed input=%s peer=%s: %s", key, config.source_node_id, error)
                    failures += 1
                    try:
                        client.disconnect()
                    except Exception:
                        pass
                    delay = min(5, max(config.period_ms / 1000, 0.2) * 2 ** min(failures - 1, 5))
                self.stop_event.wait(delay)
        finally:
            client.destroy()

    def apply_values(self):
        # All simulation writes occur on the PLC cycle, so links sharing a BOOL
        # byte preserve each other's bits. Network workers never block that cycle.
        with self.lock:
            pending, self.pending = self.pending, {}
            status = {key: dict(value) for key, value in self.status.items()}
        for config in self.configs:
            key = self.address(config)
            if key not in pending:
                continue
            buffer = self.buffers[config.db]
            if config.data_type == "BOOL":
                mask = 1 << config.bit_offset
                buffer[config.byte_offset] = buffer[config.byte_offset] | mask if pending[key] else buffer[config.byte_offset] & ~mask
            else:
                struct.pack_into(">f", buffer, config.byte_offset, pending[key])
        if self.configs:
            with open(STATUS_PATH + ".tmp", "w") as output:
                json.dump(status, output)
            os.replace(STATUS_PATH + ".tmp", STATUS_PATH)

    def safe_off_inputs(self):
        """Return fail-safe remote input addresses with waiting/stale quality."""
        now = time.time()
        with self.lock:
            statuses = {key: dict(value) for key, value in self.status.items()}
        unsafe = set()
        for config in self.configs:
            if config.stale_behavior != "force_off":
                continue
            status = statuses[self.address(config)]
            last_success = status["last_success_epoch"]
            if last_success is None or (now - last_success) * 1000 > config.stale_after_ms:
                unsafe.add((config.db, config.byte_offset,
                            config.bit_offset if config.data_type == "BOOL" else None))
        return unsafe

    def close(self):
        self.stop_event.set()
        deadline = time.monotonic() + 11
        for thread in self.threads:
            thread.join(max(0, deadline - time.monotonic()))
