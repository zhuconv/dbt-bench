#!/usr/bin/env python3
"""Offline runtime smoke using a minimal host-shell rootfs; NOT a benchmark image."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import argparse
import os
import re
import shutil
import subprocess
import tempfile

from dbt_bench.backends import find_apptainer
from dbt_bench.io import evidence_ref, file_hash, write_json, write_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "validation/evidence/apptainer-smoke.json")
    args = parser.parse_args()
    binary = find_apptainer()
    if not binary:
        parser.error("Apptainer not found; load the cluster module")
    work = Path(tempfile.mkdtemp(prefix="apptainer-smoke-", dir="/tmp"))
    rootfs = work / "rootfs"
    for directory in ("bin", "lib", "lib64", "etc", "tmp", "var/tmp", "proc", "sys", "dev", "root", "work"):
        (rootfs / directory).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop("LD_PRELOAD", None)
    env["APPTAINER_CACHEDIR"] = str(ROOT / ".cache/apptainer")
    env["APPTAINER_TMPDIR"] = str(work / "tmp")
    env["BENCH_HOST_SENTINEL"] = "must-not-leak"
    Path(env["APPTAINER_TMPDIR"]).mkdir()
    ldd = subprocess.run(["ldd", "/bin/sh"], env=env, capture_output=True, text=True, check=True)
    files = {"/bin/sh"} | set(re.findall(r"(?:=>\s*)?(/[\w/+.\-]+)", ldd.stdout))
    copied = []
    for name in sorted(files):
        source = Path(name)
        if source.is_file():
            destination = rootfs / name.lstrip("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append({"source": name, "sha256": file_hash(source)})
    (rootfs / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/sh\n")
    (rootfs / "etc/group").write_text("root:x:0:\n")
    runs = []
    for name, flags in (("unprivileged", []), ("harbor_fakeroot", ["--fakeroot", "--writable-tmpfs", "--pid"])):
        command = [str(binary), "exec", "--containall", "--cleanenv", "--no-mount", "home,cwd,hostfs,bind-paths",
                   "--pwd", "/work", *flags, str(rootfs), "/bin/sh", "-c",
                   'test -z "${BENCH_HOST_SENTINEL:-}" && printf "APPTAINER_SMOKE_OK uid=%s\\n" "$UID"']
        try:
            run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=45)
            output, code = run.stdout + run.stderr, run.returncode
        except subprocess.TimeoutExpired as exc:
            output, code = f"Timeout: {exc}", None
        log = args.output.parent / f"apptainer-smoke-{name}.log"
        write_text(log, output)
        runs.append({"mode": name, "argv": command, "returncode": code,
                     "passed": code == 0 and "APPTAINER_SMOKE_OK" in output,
                     "evidence": evidence_ref(log, ROOT)})
    result = {"scope": "offline_runtime_fixture_not_benchmark_or_harbor_trial", "runtime": str(binary),
              "rootfs_sources": copied, "rootfs": str(rootfs), "runs": runs,
              "benchmark_trials": 0, "harbor_executed": False}
    write_json(args.output, result)
    import json
    print(json.dumps({"runtime": str(binary), "runs": [{k: r[k] for k in ("mode", "returncode", "passed")} for r in runs]}, indent=2))
    return 0 if all(r["passed"] for r in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
