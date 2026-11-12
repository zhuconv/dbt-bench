#!/usr/bin/env python3
"""Install the pinned Harbor runtime and fetch three pilot tasks on a compute node.

Run this fixed preparation script in the ordinary terminal where check_execution passes.
It does not submit Slurm jobs, build images, run benchmarks, or call a model.
Every invocation retains its own command logs and summary, including partial failures.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
import shutil
import subprocess
import tomllib

from dbt_bench.io import evidence_ref, read_json, write_json

PILOTS = ("ade__ade-bench-airbnb005", "spider2__workday002", "deb__dbt-session-attribution")


def verify_harbor_lock(path, source_lock):
    """Require the independently pinned wheel in the resolved transitive lock."""
    packages = tomllib.loads(Path(path).read_text())["package"]
    entries = [p for p in packages if p["name"] == "harbor"]
    expected = source_lock["harbor"]
    if len(entries) != 1 or entries[0]["version"] != expected["version"]:
        raise ValueError("Runtime lock does not contain exactly the pinned Harbor version")
    if not any(w.get("hash") == "sha256:" + expected["wheel_sha256"]
               for w in entries[0].get("wheels", [])):
        raise ValueError("Runtime lock does not contain the independently pinned Harbor wheel hash")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="New directory for this invocation's evidence")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    output = (args.output_dir or ROOT / "artifacts/bootstrap" / now.strftime("%Y%m%dT%H%M%S.%fZ")).resolve()
    output.mkdir(parents=True, exist_ok=False)
    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    mutex = (cache / "bootstrap.lock").open("a")
    try:
        fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("Another bootstrap is preparing this workspace")
    env = {**os.environ, "UV_CACHE_DIR": str(cache / "uv"), "GIT_TERMINAL_PROMPT": "0",
           "GIT_LFS_SKIP_SMUDGE": "1"}
    # Keep the runtime at runtime/.venv even if the caller has another uv environment active.
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    result = {"schema_version": 1, "timestamp_utc": now.isoformat(), "steps": [],
              "scope": "runtime_install_and_three_pilot_source_snapshots", "pilot_tasks": list(PILOTS),
              "benchmark_trials": 0, "qwen_smoke_executed": False, "complete_task_images_built": 0,
              "source_lock": evidence_ref(ROOT / "sources.lock.yaml", ROOT),
              "script": evidence_ref(Path(__file__), ROOT), "bootstrap_complete": False}

    def save():
        write_json(output / "summary.json", result)

    def run(name, command, timeout):
        log = output / f"{name}.log"
        step = {"name": name, "argv": [str(c) for c in command]}
        print(f"[{name}] {log}", flush=True)
        with log.open("w") as stream:
            try:
                completed = subprocess.run(step["argv"], cwd=ROOT, env=env, stdout=stream,
                                           stderr=subprocess.STDOUT, timeout=timeout)
                step.update(returncode=completed.returncode, status="passed" if completed.returncode == 0 else "failed")
            except (OSError, subprocess.TimeoutExpired) as exc:
                stream.write(str(exc) + "\n")
                step.update(returncode=None, status="error", reason=str(exc))
        step["log"] = evidence_ref(log, ROOT)
        result["steps"].append(step)
        save()
        print(f"[{name}] {step['status']}", flush=True)
        return step["status"] == "passed"

    save()
    if not run("execution", [sys.executable, ROOT / "scripts/check_execution.py", "--network", "--container",
                             "--slurm", "--output", output / "execution/diagnosis.json"], 180):
        result["blocker"] = "Execution prerequisites failed in this process; use the allocated compute node's ordinary terminal"
        save()
        print(json.dumps({"bootstrap_complete": False, "summary": str(output / "summary.json")}, indent=2))
        return 1

    uv = shutil.which("uv")
    lock = ROOT / "runtime/uv.lock"
    lock_ok = False
    if uv:
        resolved = lock.is_file() or run("runtime-lock", [uv, "lock", "--project", ROOT / "runtime",
                                                          "--python", sys.executable], 1800)
        if resolved:
            try:
                verify_harbor_lock(lock, read_json(ROOT / "sources.lock.yaml"))
                result["runtime_lock"] = evidence_ref(lock, ROOT)
                lock_ok = True
            except (OSError, ValueError, KeyError) as exc:
                result["runtime_lock_error"] = str(exc)
    else:
        result["runtime_lock_error"] = "uv executable missing"
    installed = lock_ok and run("runtime-sync", [uv, "sync", "--project", ROOT / "runtime", "--locked",
                                                "--python", sys.executable], 1800)
    result["runtime_installed"] = bool(installed)
    if installed and read_json(ROOT / "sources.lock.yaml").get("runtime_patches"):
        result["runtime_patches_applied"] = run("runtime-patches", [ROOT / "runtime/.venv/bin/python",
                                                                  ROOT / "scripts/apply_runtime_patches.py"], 120)
    save()

    # Source preparation is independent of converter conformance and runtime installation.
    command = [sys.executable, ROOT / "scripts/prepare.py", "tasks", "--fetch"]
    for task in PILOTS:
        command.extend(["--task", task])
    prepared = run("pilot-sources", command, 10800)
    prepare_result = ROOT / "artifacts/prepare/result.json"
    if prepare_result.exists():
        snapshot = output / "pilot-sources.json"
        write_json(snapshot, read_json(prepare_result))
        result["preparation"] = evidence_ref(snapshot, ROOT)
    conformance = False
    if installed:
        command = [ROOT / "runtime/.venv/bin/python", ROOT / "scripts/check_runtime.py",
                   "--output", output / "runtime-conformance.json"]
        for task in PILOTS:
            if (ROOT / "tasks" / task / "task.toml").exists():
                command.extend(["--task", ROOT / "tasks" / task])
        conformance = run("runtime-conformance", command, 120)
    checking_python = ROOT / "runtime/.venv/bin/python" if installed else sys.executable
    ledger = run("validation-ledger", [checking_python, ROOT / "scripts/validate.py", "--backend", "apptainer"], 300)
    report = run("report", [checking_python, ROOT / "scripts/report.py"], 300)
    result.update(pilot_sources_prepared=prepared, runtime_conformance_passed=conformance,
                  bootstrap_complete=bool(installed and prepared and conformance and ledger and report
                                          and result.get("runtime_patches_applied", True)),
                  next_step="Review native task builds and prepare complete aarch64 SIFs; runtime fixtures are not benchmark evidence")
    save()
    print(json.dumps({k: result[k] for k in ("bootstrap_complete", "runtime_installed", "pilot_sources_prepared",
                                            "runtime_conformance_passed", "benchmark_trials")}
                     | {"summary": str(output / "summary.json")}, indent=2))
    return 0 if result["bootstrap_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
