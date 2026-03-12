#!/usr/bin/env python3
"""
Read load-test.json (measurements.tasks.<task>.<step>.memory/cpu), optionally
get-pod-step-names.json for row order. Write get-task-step-resources.json/html.
Inject measurements.stable_task_steps for Horreum (stable keys per run).
"""

import argparse
import html
import json
import os
import sys

# Stable task keys for Horreum (same every run). Suffix match: *-build-container -> build-container.
STABLE_SUFFIXES = [
    "build-container", "build-image-index", "build-source-image",
    "collect-data", "verify-conforma", "prefetch-dependencies",
    "apply-tags", "push-dockerfile", "clone-repository", "init",
    "clair-scan", "clamav-scan", "rpms-signature-scan",
    "sast-shell-check", "sast-snyk-check", "sast-unicode-check",
    "show-sbom", "deprecated-base-image-check", "coverity-availability-check",
]
STABLE_SUFFIXES_SORTED = sorted(STABLE_SUFFIXES, key=len, reverse=True)

# Tekton task short name (segment after last _) -> stable key when suffix does not match.
TASK_ALIAS = {
    "buildah-oci-ta": "build-container",
    "source-build-oci-ta": "build-source-image",
    "git-clone-oci-ta": "clone-repository",
    "push-dockerfile-oci-ta": "push-dockerfile",
    "prefetch-dependencies-oci-ta": "prefetch-dependencies",
    "verify-conforma-konflux-ta": "verify-conforma",
    "sast-shell-check-oci-ta": "sast-shell-check",
    "sast-snyk-check-oci-ta": "sast-snyk-check",
    "sast-unicode-check-oci-ta": "sast-unicode-check",
}


def stable_task_type(task_name):
    """Map run-specific task name to stable key (e.g. build-container) for Horreum."""
    if not task_name:
        return None
    for s in STABLE_SUFFIXES_SORTED:
        if task_name.endswith("-" + s) or task_name.endswith("_" + s):
            return s
    # Tekton names like build_buildah-oci-ta: use segment after last _.
    if "_" in task_name:
        short = task_name.split("_")[-1]
        if short in TASK_ALIAS:
            return TASK_ALIAS[short]
    return None


def _metric_str(v):
    if v is None:
        return "Prometheus didn't return data"
    if isinstance(v, dict):
        if v.get("mean") is not None:
            return str(v["mean"])
        if "value" in v:
            return str(v["value"])
        return "Prometheus didn't return data" if not v else str(v)
    return str(v)


def build_stable_task_steps(measurements):
    """From measurements.tasks.<task>.<step> build stable_task_steps[stable_type][step] = {memory, cpu}. First wins."""
    out = {}
    tasks = (measurements or {}).get("tasks") if isinstance(measurements, dict) else {}
    if not isinstance(tasks, dict):
        return out
    for task_name, steps in tasks.items():
        if not isinstance(steps, dict):
            continue
        stable = stable_task_type(task_name)
        if not stable:
            continue
        if stable not in out:
            out[stable] = {}
        for step_name, m in steps.items():
            if isinstance(m, dict) and step_name not in out[stable]:
                out[stable][step_name] = {"memory": m.get("memory"), "cpu": m.get("cpu")}
    return out


def collect_nested(measurements):
    """Collect (task, step) -> {memory, cpu} from measurements.tasks.<task>.<step>."""
    out = {}
    tasks = (measurements or {}).get("tasks") if isinstance(measurements, dict) else {}
    if not isinstance(tasks, dict):
        return out
    for task_name, steps in tasks.items():
        if not isinstance(steps, dict):
            continue
        for step_name, m in steps.items():
            if isinstance(m, dict):
                out[(task_name, step_name)] = {
                    "memory": _metric_str(m.get("memory")),
                    "cpu": _metric_str(m.get("cpu")),
                }
    return out


def main():
    ap = argparse.ArgumentParser(description="Task/step Memory and CPU from load-test.json; output JSON/HTML; inject stable_task_steps for Horreum.")
    ap.add_argument("--load-test-json", default="load-test.json", help="Path to load-test.json")
    ap.add_argument("--pod-step-json", default="get-pod-step-names.json", help="Path to get-pod-step-names.json (optional)")
    ap.add_argument("--artifact-dir", required=True, help="Artifact dir for inputs and outputs")
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

    # Inject stable_task_steps for Horreum (fixed JSONPaths per run).
    stable = build_stable_task_steps(measurements)
    if stable:
        data.setdefault("measurements", {})["stable_task_steps"] = stable
        with open(load_test_path, "w") as f:
            json.dump(data, f, indent=2)

    collected = collect_nested(measurements)

    expected = []
    if os.path.isfile(pod_step_path):
        try:
            with open(pod_step_path) as f:
                for e in (json.load(f) or {}).get("pods", []):
                    t = (e.get("task_name") or e.get("pod_id", "")).replace(".", "_").replace("/", "_")
                    for s in e.get("steps", []):
                        expected.append((t, s.replace(".", "_")))
        except Exception:
            pass

    keys = expected if expected else sorted(collected.keys())
    seen = set()
    rows = []
    for k in keys:
        task, step = (k, "") if not isinstance(k, tuple) else k
        seen.add((task, step))
        r = collected.get((task, step), {})
        rows.append((task, step, r.get("memory", "Prometheus didn't return data"), r.get("cpu", "Prometheus didn't return data")))
    if expected:
        for k in sorted(collected.keys()):
            if k not in seen:
                task, step = k
                r = collected[k]
                rows.append((task, step, r.get("memory", "Prometheus didn't return data"), r.get("cpu", "Prometheus didn't return data")))

    if not rows:
        rows = [("(no task/step metrics found)", "", "Prometheus didn't return data", "Prometheus didn't return data")]

    table = [{"task": t, "step": s, "memory": m, "cpu": c} for t, s, m, c in rows]
    with open(os.path.join(base, "get-task-step-resources.json"), "w") as f:
        json.dump({"rows": table}, f, indent=2)

    # HTML with rowspan for same task
    parts = []
    i = 0
    while i < len(rows):
        task, step, mem, cpu = rows[i]
        j = i + 1
        while j < len(rows) and rows[j][0] == task:
            j += 1
        span = j - i
        parts.append(f'    <tr><td rowspan="{span}">{html.escape(task)}</td><td>{html.escape(step)}</td><td>{html.escape(mem)}</td><td>{html.escape(cpu)}</td></tr>\n')
        for k in range(i + 1, j):
            _, step, mem, cpu = rows[k]
            parts.append(f"    <tr><td>{html.escape(step)}</td><td>{html.escape(mem)}</td><td>{html.escape(cpu)}</td></tr>\n")
        i = j

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Task / Step resources</title>
<style>table {{ border-collapse: collapse; }} th, td {{ border: 1px solid #333; padding: 6px 10px; text-align: left; }} th {{ background: #eee; }} td[rowspan] {{ vertical-align: top; }}</style>
</head>
<body><h1>Task / Step resources (Memory, CPU)</h1>
<table><thead><tr><th>Task</th><th>Step</th><th>Memory</th><th>CPU</th></tr></thead>
<tbody>
{"".join(parts)}</tbody></table></body></html>
"""
    with open(os.path.join(base, "get-task-step-resources.html"), "w") as f:
        f.write(html_out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
