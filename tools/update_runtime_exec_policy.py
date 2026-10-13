"""Refresh fixed runtime script fingerprints; rebuild the Docker gateway afterwards."""
import ast
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
policy_path = root / "docker_proxy/runtime_exec_policy.json"
rules = json.loads(policy_path.read_text())
for rule in rules:
    tree = ast.parse((root / "backend/services" / rule["source"]).read_text())
    script = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                  if isinstance(node, ast.Assign) and any(
                      isinstance(target, ast.Name) and target.id == rule["constant"]
                      for target in node.targets))
    rule["sha256"] = hashlib.sha256(script.encode()).hexdigest()
policy_path.write_text(json.dumps(rules, indent=2) + "\n")
print(f"Updated {len(rules)} fixed operation fingerprints")
