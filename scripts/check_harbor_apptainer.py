#!/usr/bin/env python3
"""Probe an actual native Harbor SingularityEnvironment using a built task SIF.

This is an environment integration check, not a Qwen run or a benchmark score.
It uses a fresh writable overlay and never installs gold, solutions, or tests.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import argparse
import asyncio
from datetime import datetime, timezone
import importlib.metadata
import json
import logging
import os
import shlex
import traceback

from dbt_bench.backends import harbor_process_env, require_runtime
from dbt_bench.io import evidence_ref, file_hash, tree_hash, write_json


async def probe(args):
    from harbor.environments.singularity.singularity import SingularityEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths

    if importlib.metadata.version("harbor") != "0.25.0":
        raise ValueError("Expected the pinned Harbor 0.25.0 runtime")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    task = Task(task_dir=args.task)
    image = args.image.resolve()
    runtime = require_runtime("apptainer")
    os.environ["PATH"] = harbor_process_env("apptainer")["PATH"]
    logger = logging.getLogger("dbt-bench-harbor-probe")
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(output / "harbor-environment.log")
    logger.addHandler(handler)
    paths = TrialPaths(trial_dir=output / "environment")
    paths.mkdir()
    config = task.config.environment.model_copy(update={"docker_image": str(image)})
    environment = SingularityEnvironment(environment_dir=task.paths.environment_dir,
        environment_name="dbt-bench-preflight", session_id=output.name, trial_paths=paths,
        task_env_config=config, persistent_env={"DB_TYPE": "duckdb"}, logger=logger,
        singularity_no_mount="home,tmp,cwd,hostfs,bind-paths")
    result = {"schema_version": 1, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "native_Harbor_environment_integration_only_not_Qwen_or_benchmark_verification",
        "harbor_version": importlib.metadata.version("harbor"), "runtime": runtime,
        "task_hash": tree_hash(args.task), "image": evidence_ref(image, ROOT),
        "probe": evidence_ref(ROOT / "scripts/context_probe.py", ROOT),
        "integration_script": evidence_ref(Path(__file__), ROOT),
        "benchmark_trials": 0, "qwen_smoke_executed": False, "started": False, "checks": []}

    async def check(name, command, user=None):
        execution = await environment.exec(command, timeout_sec=90, user=user)
        record = {"name": name, "command": command, "user": user, "result": execution.model_dump(mode="json")}
        result["checks"].append(record)
        write_json(output / f"{name}.json", record)
        print(f"{name}: rc={execution.return_code}", flush=True)
        return execution

    try:
        await asyncio.wait_for(environment.start(force_build=False), timeout=300)
        result["started"] = True
        if task.config.environment.healthcheck:
            await check("native-healthcheck", task.config.environment.healthcheck.command)
        await environment.upload_file(ROOT / "scripts/context_probe.py", "/tmp/dbt-bench-context-probe.py")
        command = "python3 /tmp/dbt-bench-context-probe.py --db " + shlex.quote(args.db)
        await check("default-context", command)
        await check("login-context", "bash -lc " + shlex.quote(command))
        await check("explicit-root-context", command, user="root")
        visibility = ("test ! -e /solution && test ! -e /tests && test ! -e "
                      + shlex.quote(str(ROOT.parent / "training"))
                      + " && printf 'KNOWN_HIDDEN_PATHS_ABSENT\\n'")
        await check("known-hidden-paths", visibility)
    except Exception as exc:
        result["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
    finally:
        try:
            await environment.stop(delete=True)
        except Exception as exc:
            result["cleanup_error"] = str(exc)
        handler.flush()
        handler.close()
        logger.removeHandler(handler)
        result["environment_log"] = evidence_ref(output / "harbor-environment.log", ROOT)
        result["image_unchanged"] = file_hash(image) == result["image"]["sha256"]
        failures = [c["name"] for c in result["checks"] if c["result"]["return_code"] != 0]
        for c in result["checks"]:
            if c["name"].endswith("context") and c["result"]["return_code"] == 0:
                try:
                    data = json.loads(c["result"]["stdout"])
                    if data.get("duckdb_error") or not data.get("databases") or any(d.get("error") for d in data["databases"]):
                        failures.append(c["name"] + ":database_or_import_failure")
                except (ValueError, TypeError):
                    failures.append(c["name"] + ":unparseable_probe_output")
        result["failed_checks"] = failures
        result["integration_passed"] = bool(result["started"] and result["checks"] and not failures
                                             and not result.get("error") and not result.get("cleanup_error")
                                             and result["image_unchanged"])
        write_json(output / "result.json", result)
    print(json.dumps({"integration_passed": result["integration_passed"], "started": result["started"],
                      "failed_checks": failures, "output": str(output / "result.json")}, indent=2))
    return 0 if result["integration_passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--db", required=True, help="Explicit native task database path inside the container")
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory for immutable evidence")
    return asyncio.run(probe(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
