#!/usr/bin/env python3
"""Record host capabilities. This is not an agent/verifier preflight certificate."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
import shutil
import socket
import subprocess

from dbt_bench.io import write_json
from dbt_bench.backends import find_apptainer, runtime_info
from check_execution import permission_denied, probe_sockets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "validation/evidence/host.json")
    args = parser.parse_args()
    result = {"scope": "host_only_not_task_execution", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "python": {"path": sys.executable, "version": sys.version},
              "commands": {c: shutil.which(c) for c in ("docker", "harbor", "apptainer", "singularity", "git", "uv")},
              "container_runtimes": {"apptainer": runtime_info("apptainer"), "docker": runtime_info("docker")},
              "cluster": {"slurm_job_id": os.environ.get("SLURM_JOB_ID"), "node": os.environ.get("SLURM_JOB_NODELIST")},
              "runtime_probe_scope": "Executable/version only; container launch requires a separate smoke test",
              "endpoint_configuration": {key: bool(os.environ.get(key)) for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "DBT_BENCH_MODEL")},
              "dns": {}, "qwen_smoke_executed": False}
    if find_apptainer() and not result["commands"]["apptainer"]:
        result["module_hint"] = "module load tacc-apptainer/1.4.1"
    result["sockets"] = probe_sockets()
    blocked = permission_denied(result["sockets"])
    result["network_probe_scope"] = "current_process_only_not_cluster_network_health"
    for host in ("pypi.org", "github.com", "huggingface.co", "registry.npmjs.org"):
        if blocked:
            result["dns"][host] = "not_tested_local_socket_permission_denied"
            continue
        try:
            socket.getaddrinfo(host, 443)
            result["dns"][host] = "resolved"
        except OSError as exc:
            result["dns"][host] = str(exc)
    try:
        result["harbor_version"] = importlib.metadata.version("harbor")
    except importlib.metadata.PackageNotFoundError:
        result["harbor_version"] = None
    if result["commands"]["docker"]:
        try:
            p = subprocess.run(["docker", "info", "--format", "{{json .ServerVersion}}"], capture_output=True, text=True, timeout=20)
            result["docker_daemon"] = {"returncode": p.returncode, "version": p.stdout.strip(), "error": p.stderr.strip()}
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["docker_daemon"] = {"error": str(exc)}
    write_json(args.output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
