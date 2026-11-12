#!/usr/bin/env python3
"""Diagnose compute allocation, syscall permission, network, and Apptainer separately.

Run in a normal terminal on the allocated compute node to compare it with tool execution.
This script neither changes sandbox permissions nor submits jobs or benchmark trials.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import argparse
from datetime import datetime, timezone
import errno
import json
import os
import shutil
import socket
import subprocess
import urllib.error
import urllib.request

from dbt_bench.backends import runtime_info
from dbt_bench.io import evidence_ref, read_json, write_json, write_text


def process_restrictions():
    fields = {"NoNewPrivs", "Seccomp", "Seccomp_filters", "CapEff", "CapBnd"}
    result = {}
    status = Path("/proc/self/status")
    if status.exists():
        result = {key: value.strip() for line in status.read_text().splitlines()
                  if ":" in line for key, value in [line.split(":", 1)] if key in fields}
    for name in ("uid_map", "gid_map", "cgroup"):
        path = Path("/proc/self") / name
        result[name] = path.read_text().strip() if path.exists() else None
    return result


def probe_sockets():
    probes = []
    for name, family, kind in (("unix_stream", socket.AF_UNIX, socket.SOCK_STREAM),
                               ("ipv4_stream", socket.AF_INET, socket.SOCK_STREAM),
                               ("ipv4_datagram", socket.AF_INET, socket.SOCK_DGRAM)):
        result = {"probe": name, "created": False, "getsockopt_ok": False}
        stage = "socket"
        try:
            with socket.socket(family, kind) as connection:
                result["created"] = True
                stage = "getsockopt"
                result["socket_type"] = connection.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
                result["getsockopt_ok"] = True
        except OSError as exc:
            result["error"] = {"stage": stage, "errno": exc.errno, "message": str(exc)}
        probes.append(result)
    return probes


def permission_denied(probes):
    return any(p.get("error", {}).get("errno") in {errno.EPERM, errno.EACCES} for p in probes)


def network_checks(probes):
    if permission_denied(probes):
        return {"status": "blocked_by_socket_permissions", "checks": [],
                "reason": "Do not diagnose DNS or remote service availability when local socket operations are denied"}
    checks = []
    urls = ("https://pypi.org/pypi/harbor/0.25.0/json",
            "https://github.com/harbor-framework/harbor",
            "https://huggingface.co/datasets/harborframework/harbor-datasets")
    for url in urls:
        record = {"url": url}
        try:
            request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "dbt-bench-execution-check/1"})
            with urllib.request.urlopen(request, timeout=8) as response:
                record.update(status="reachable", http_status=response.status)
        except urllib.error.HTTPError as exc:
            record.update(status="http_error", http_status=exc.code, reason=str(exc))
        except (OSError, urllib.error.URLError) as exc:
            record.update(status="unreachable", reason=str(exc))
        checks.append(record)
    return {"status": "reachable" if all(c["status"] == "reachable" for c in checks) else "needs_review", "checks": checks}


def diagnose(record):
    reasons = []
    if not record["allocation"]["job_id"]:
        reasons.append("no_slurm_allocation_in_environment")
    if permission_denied(record["sockets"]):
        reasons.append("process_socket_operations_denied")
    if not record["apptainer"]["available"]:
        reasons.append("apptainer_executable_unavailable")
    if record.get("network", {}).get("status") != "reachable":
        reasons.append("download_access_not_confirmed")
    smoke = record.get("container_smoke", {})
    if not smoke.get("runs") or not all(run.get("passed") for run in smoke["runs"]):
        reasons.append("apptainer_execution_not_confirmed")
    if record.get("slurm_query", {}).get("status") != "confirmed":
        reasons.append("live_allocation_not_confirmed")
    return {"execution_conditions_ready": not reasons, "blockers": reasons,
            "scope": "Execution prerequisites only; not Harbor/task/model readiness"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/execution/diagnosis.json")
    parser.add_argument("--network", action="store_true", help="Bounded HTTPS reachability checks, only if socket permissions allow")
    parser.add_argument("--container", action="store_true", help="Run the existing offline Apptainer fixture if socket operations allow")
    parser.add_argument("--slurm", action="store_true", help="Confirm the inherited allocation with a bounded scontrol query")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    record = {"scope": "current_process_execution_conditions", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "hostname": socket.gethostname(), "python": sys.executable,
              "allocation": {"job_id": os.environ.get("SLURM_JOB_ID"), "nodes": os.environ.get("SLURM_JOB_NODELIST"),
                             "cpus_on_node": os.environ.get("SLURM_CPUS_ON_NODE")},
              "restrictions": process_restrictions(), "sockets": probe_sockets(),
              "apptainer": runtime_info("apptainer"), "benchmark_trials": 0}
    blocked = permission_denied(record["sockets"])
    record["network"] = network_checks(record["sockets"]) if args.network else {"status": "not_requested"}
    record["slurm_query"] = {"status": "not_requested"}
    if args.slurm:
        job_id = record["allocation"]["job_id"]
        if blocked:
            record["slurm_query"] = {"status": "blocked_by_socket_permissions"}
        elif not job_id or not shutil.which("scontrol"):
            record["slurm_query"] = {"status": "missing_job_or_scontrol"}
        else:
            command = ["scontrol", "show", "job", job_id, "--oneliner"]
            try:
                p = subprocess.run(command, capture_output=True, text=True, timeout=8)
                log = output.parent / "slurm-query.log"
                write_text(log, p.stdout + p.stderr)
                record["slurm_query"] = {"status": "confirmed" if p.returncode == 0 and "JobState=RUNNING" in p.stdout else "needs_review",
                                         "returncode": p.returncode, "evidence": evidence_ref(log, ROOT)}
            except subprocess.TimeoutExpired:
                record["slurm_query"] = {"status": "timeout", "timeout_sec": 8}
    record["container_smoke"] = {"status": "not_requested"}
    if args.container:
        if blocked:
            record["container_smoke"] = {"status": "blocked_by_socket_permissions", "reason": "Existing smoke failed at the same denied getsockopt; no repeated launch"}
        elif not record["apptainer"]["available"]:
            record["container_smoke"] = {"status": "runtime_unavailable"}
        else:
            smoke_output = output.parent / "apptainer-smoke.json"
            try:
                p = subprocess.run([sys.executable, str(ROOT / "scripts/apptainer_smoke.py"), "--output", str(smoke_output)],
                                   capture_output=True, text=True, timeout=110)
                log = output.parent / "container-smoke-driver.log"
                write_text(log, p.stdout + p.stderr)
                record["container_smoke"] = read_json(smoke_output) if smoke_output.exists() else {"runs": []}
                record["container_smoke"].update(returncode=p.returncode, driver_evidence=evidence_ref(log, ROOT))
            except subprocess.TimeoutExpired:
                record["container_smoke"] = {"status": "timeout", "timeout_sec": 110}
    record["diagnosis"] = diagnose(record)
    write_json(output, record)
    print(json.dumps({"hostname": record["hostname"], "allocation": record["allocation"],
                      "diagnosis": record["diagnosis"], "output": str(output)}, indent=2))
    return 0 if record["diagnosis"]["execution_conditions_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
