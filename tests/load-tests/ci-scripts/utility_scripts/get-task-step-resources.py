#!/usr/bin/env python3
"""
Build Task | Step | Memory | CPU report from load-test.json.

Reads load-test.json and walks measurements: keys like "tasks[<task>]" with
nested "step[<step>]" dicts containing memory/cpu. Optionally uses
get-pod-step-names.json for row order. Writes get-task-step-resources.json
and get-task-step-resources.html under --artifact-dir.
"""

import argparse
import html
import json
import os
import sys


def get_measurement_value(data):
    """Return display string: mean if present, else value or 'Prometheus didn't return data'."""
    if data is None:
        return "Prometheus didn't return data"
    if isinstance(data, dict):
        if "mean" in data and data["mean"] is not None:
            return str(data["mean"])
        if "value" in data:
            return str(data["value"])
        if not data:
            return "Prometheus didn't return data"
        return str(data)
    return str(data)


def collect_task_step_metrics(measurements):
    """
    Walk measurements dict: keys "tasks[taskname]" -> { "step[stepname]": { memory, cpu } }.
    Returns dict (task, step) -> { "memory": str, "cpu": str }.
    """
    out = {}
    if not isinstance(measurements, dict):
        return out
    task_prefix = "tasks["
    task_suffix = "]"
    for key, step_dict in measurements.items():
        if not isinstance(key, str) or not key.startswith(task_prefix) or task_suffix not in key:
            continue
        task_name = key[len(task_prefix) : key.index(task_suffix)]
        if not isinstance(step_dict, dict):
            continue
        for step_key, metric_dict in step_dict.items():
            if not isinstance(metric_dict, dict):
                continue
            step_name = step_key[5:-1] if (step_key.startswith("step[") and step_key.endswith("]")) else step_key
            mem = metric_dict.get("memory")
            cpu = metric_dict.get("cpu")
            out[(task_name, step_name)] = {
                "memory": get_measurement_value(mem) if mem is not None else "Prometheus didn't return data",
                "cpu": get_measurement_value(cpu) if cpu is not None else "Prometheus didn't return data",
            }
    return out


def main():
    ap = argparse.ArgumentParser(description="Build task/step Memory and CPU report from load-test.json.")
    ap.add_argument("--load-test-json", default="load-test.json", help="Path to load-test.json")
    ap.add_argument("--pod-step-json", default="get-pod-step-names.json", help="Path to get-pod-step-names.json (optional)")
    ap.add_argument("--artifact-dir", required=True, help="Directory for inputs and outputs")
    args = ap.parse_args()

    base = args.artifact_dir
    load_test_path = os.path.join(base, args.load_test_json)
    pod_step_path = os.path.join(base, args.pod_step_json)

    if not os.path.isfile(load_test_path):
        print("Error: load-test.json not found at", load_test_path, file=sys.stderr)
        return 1

    with open(load_test_path) as f:
        data = json.load(f)

    measurements = data.get("measurements") or {}
    collected = collect_task_step_metrics(measurements)

    expected = []
    if os.path.isfile(pod_step_path):
        try:
            with open(pod_step_path) as f:
                pod_data = json.load(f)
            for entry in pod_data.get("pods", []):
                task_name = entry.get("task_name") or entry.get("pod_id", "")
                for step in entry.get("steps", []):
                    expected.append((task_name, step))
        except Exception:
            pass

    if expected:
        rows = []
        seen = set()
        for task, step in expected:
            key = (task, step)
            seen.add(key)
            row = collected.get(key, {})
            rows.append((task, step, row.get("memory", "Prometheus didn't return data"), row.get("cpu", "Prometheus didn't return data")))
        for key in sorted(collected):
            if key not in seen:
                task, step = key
                row = collected[key]
                rows.append((task, step, row.get("memory", "Prometheus didn't return data"), row.get("cpu", "Prometheus didn't return data")))
    else:
        rows = [
            (task, step, row.get("memory", "Prometheus didn't return data"), row.get("cpu", "Prometheus didn't return data"))
            for (task, step), row in sorted(collected.items())
        ]

    if not rows:
        rows = [("(no task/step metrics found)", "", "Prometheus didn't return data", "Prometheus didn't return data")]

    table = [{"task": task, "step": step, "memory": mem, "cpu": cpu} for task, step, mem, cpu in rows]

    json_path = os.path.join(base, "get-task-step-resources.json")
    with open(json_path, "w") as f:
        json.dump({"rows": table}, f, indent=2)

    html_row_parts = []
    i = 0
    while i < len(rows):
        task, step, mem, cpu = rows[i]
        j = i + 1
        while j < len(rows) and rows[j][0] == task:
            j += 1
        rowspan = j - i
        html_row_parts.append(
            f"    <tr><td rowspan=\"{rowspan}\">{html.escape(task)}</td>"
            f"<td>{html.escape(step)}</td><td>{html.escape(mem)}</td><td>{html.escape(cpu)}</td></tr>\n"
        )
        for k in range(i + 1, j):
            _, step, mem, cpu = rows[k]
            html_row_parts.append(
                f"    <tr><td>{html.escape(step)}</td><td>{html.escape(mem)}</td><td>{html.escape(cpu)}</td></tr>\n"
            )
        i = j

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Task / Step resources (Memory, CPU)</title>
  <style>
    table {{ border-collapse: collapse; }}
    th, td {{ border: 1px solid #333; padding: 6px 10px; text-align: left; }}
    th {{ background: #eee; }}
    td[rowspan] {{ vertical-align: top; }}
  </style>
</head>
<body>
  <h1>Task / Step resources (Memory, CPU)</h1>
  <table>
    <thead>
      <tr><th>Task</th><th>Step</th><th>Memory</th><th>CPU</th></tr>
    </thead>
    <tbody>
{''.join(html_row_parts)}    </tbody>
  </table>
</body>
</html>
"""
    html_path = os.path.join(base, "get-task-step-resources.html")
    with open(html_path, "w") as f:
        f.write(html_content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
