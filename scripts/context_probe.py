#!/usr/bin/env python3
"""Run inside each real execution context; never print API keys or mutate source databases.

Supply --db and --query explicitly; no database discovery or guessed output contract.
This probe is evidence collection, not a claim of Qwen tool-context equivalence.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys


def run(command):
    try:
        p = subprocess.run(command, text=True, capture_output=True, timeout=60)
        return {"argv": command, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": command, "error": str(exc)}


def probe(databases=(), query="SELECT 1"):
    dbt = shutil.which("dbt")
    shebang = None
    if dbt:
        with open(dbt, "rb") as f:
            shebang = f.readline(1024).decode(errors="replace").strip()
    result = {"user": pwd.getpwuid(os.getuid()).pw_name, "uid": os.getuid(), "gid": os.getgid(),
              "cwd": os.getcwd(), "shell": os.environ.get("SHELL"), "path": os.environ.get("PATH"),
              "python": {"executable": sys.executable, "version": sys.version, "sys_path": sys.path},
              "dbt": {"path": dbt, "shebang": shebang, "version": run([dbt, "--version"]) if dbt else None},
              "environment": {k: os.environ.get(k) for k in ("DB_TYPE", "DBT_PROFILES_DIR", "DBT_PROJECT_DIR", "VIRTUAL_ENV", "PYTHONPATH")},
              "databases": []}
    for name in ("duckdb", "dbt"):
        try:
            spec = importlib.util.find_spec(name)
            result[name + "_module"] = {"origin": spec.origin if spec else None,
                "paths": list(spec.submodule_search_locations or []) if spec else []}
        except (ValueError, ModuleNotFoundError) as exc:
            result[name + "_module"] = {"error": str(exc)}
    try:
        import duckdb
        result["duckdb_version"] = duckdb.__version__
        for path in databases:
            p = Path(path)
            info = {"path": str(p.resolve()), "exists": p.exists(),
                    "readable": os.access(p, os.R_OK), "writable": os.access(p, os.W_OK)}
            try:
                with duckdb.connect(str(p), read_only=True) as con:
                    info["query"] = query
                    info["rows"] = con.execute(query).fetchall()
            except Exception as exc:
                info["error"] = str(exc)
            result["databases"].append(info)
    except ImportError as exc:
        result["duckdb_error"] = str(exc)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", action="append", default=[])
    parser.add_argument("--query", default="SELECT 1")
    args = parser.parse_args()
    print(json.dumps(probe(args.db, args.query), indent=2, default=str))
