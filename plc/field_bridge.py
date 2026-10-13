"""Asynchronous internal signal wiring, applied on the device's local cycle."""
import json
import threading
import time
import urllib.request
import os


def quality_for(address):
    try:
        with open('/tmp/anvil-field-links.json') as source:
            data = json.load(source).get(address)
    except (OSError, ValueError):
        return None
    if data is None:
        return None
    last = data.pop('last_success', None)
    age = (time.time() - last) * 1000 if last is not None else None
    return {**data, 'age_ms': round(age) if age is not None else None,
            'quality': 'waiting' if last is None else 'stale' if age > 2000 else 'good'}


class FieldBridge:
    def __init__(self, config, stop_event):
        self.config = config
        self.stop = stop_event
        self.interval = max(0.01, config.get("scan_cycle_ms", 100) / 1000)
        self.lock = threading.Lock()
        self.outputs = {}
        self.inputs = {}
        self.last_success = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.publish_quality()
        self.thread.start()

    def publish_quality(self):
        data = {binding['address']: {'last_success': self.last_success,
                'source_node_id': binding.get('field_id'), 'source_address': binding.get('field_port')}
                for binding in self.config['bindings']}
        with open('/tmp/anvil-field-links.tmp', 'w') as target:
            json.dump(data, target)
        os.replace('/tmp/anvil-field-links.tmp', '/tmp/anvil-field-links.json')

    def exchange(self, outputs):
        with self.lock:
            self.outputs = dict(outputs)
            # Keep last input values if the field runtime disappears.
            return dict(self.inputs)

    def run(self):
        while not self.stop.is_set():
            with self.lock:
                payload = {"node_id": self.config["node_id"], "outputs": dict(self.outputs)}
            try:
                req = urllib.request.Request(self.config["url"], data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=.5) as response:
                    values = json.load(response)["inputs"]
                with self.lock:
                    self.inputs = values
                    self.last_success = time.time()
            except (OSError, ValueError, KeyError):
                pass
            self.publish_quality()
            self.stop.wait(self.interval)
