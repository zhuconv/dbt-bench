"""Fetch exact source revisions and preserve native Harbor task layouts."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
import urllib.parse

from .catalog import import_legacy, load_catalog
from .backends import BACKENDS, binding_identity, configure_backend, register_sif, resolve_task
from .io import (contained, digest, evidence_ok, evidence_ref, file_hash, read_json, tree_hash,
                 tree_manifest, write_json, write_jsonl)


def command(args, **kwargs):
    p = subprocess.run(args, text=True, capture_output=True, timeout=kwargs.pop("timeout", 300), **kwargs)
    if p.returncode:
        raise RuntimeError(f"{args[0]} failed ({p.returncode}): {p.stderr[-2000:]}")
    return p.stdout.strip()


def source_is_dirty(destination):
    prefix = ["git", "-C", str(destination)]
    if not command([*prefix, "status", "--porcelain", "--untracked-files=all"]):
        return False
    # Some sparse/LFS worktrees retain a stat-only M after pointer materialization.
    # Compare actual filtered contents and index changes before declaring source drift.
    if command([*prefix, "ls-files", "--others", "--exclude-standard"]):
        return True
    for args in (["diff", "--quiet", "--no-ext-diff"], ["diff", "--cached", "--quiet", "--no-ext-diff"]):
        result = subprocess.run([*prefix, *args], capture_output=True, timeout=300)
        if result.returncode == 1:
            return True
        if result.returncode:
            raise RuntimeError("Cannot verify source cleanliness: " + result.stderr.decode(errors="replace")[-1000:])
    return False


def hydrate_hf_blobs(spec, destination, task_paths):
    """Hydrate sparse Git blobs via revision-pinned HF raw URLs, verifying Git OIDs.

    HF's Git endpoint can serve the commit/tree but fail a promisor blob request.
    Raw URLs preserve LFS pointer bytes; native git-lfs resolves their SHA-256 later.
    The Git tree remains authoritative for paths, mode bits, and expected contents.
    """
    revision = spec["revision"]
    objects = {}
    directories = {""}
    for selected in task_paths:
        directories.update(str(p) if str(p) != "." else "" for p in Path(selected).parents)
        listing = command(["git", "-C", str(destination), "ls-tree", "-rz", revision, "--", selected])
        for entry in listing.split("\0"):
            if entry:
                header, path = entry.split("\t", 1)
                mode, kind, oid = header.split()
                if kind != "blob":
                    raise ValueError(f"Unsupported Git object at {path}: {kind}")
                objects[oid] = path
    # Cone-mode sparse checkouts also materialize files directly in ancestor directories.
    for directory in sorted(directories):
        tree = revision + (":" + directory if directory else "")
        listing = command(["git", "-C", str(destination), "ls-tree", "-z", tree])
        for entry in listing.split("\0"):
            if entry:
                header, name = entry.split("\t", 1)
                mode, kind, oid = header.split()
                if kind == "blob":
                    objects[oid] = (directory + "/" if directory else "") + name
    if not objects:
        raise ValueError("No files in the selected fixed HF tree")
    cache = Path(destination) / ".git/dbt-bench-http-blobs"
    cache.mkdir(parents=True, exist_ok=True)
    request_lock = threading.Lock()
    next_request = [0.0]

    def read_remote(url):
        # HF's anonymous resolver quota is shared across these workers. Keep requests
        # below the observed fixed-window limit; cached, verified blobs need no HTTP.
        for attempt in range(4):
            with request_lock:
                delay = next_request[0] - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                next_request[0] = time.monotonic() + 0.25
            try:
                with urllib.request.urlopen(url, timeout=120) as response:
                    resolved = response.headers.get("x-repo-commit")
                    if resolved and resolved != revision:
                        raise ValueError("HF response revision mismatch")
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 3:
                    raise
                delay = rate_limit_delay(exc.headers)
                with request_lock:
                    next_request[0] = max(next_request[0], time.monotonic() + delay)
                print(f"HF rate limited; wait {delay:.0f}s before retrying pinned blob", flush=True)

    def hydrate(item):
        oid, path = item
        cached = cache / oid
        if cached.exists():
            data = cached.read_bytes()
        else:
            url = spec["url"].rstrip("/") + "/raw/" + revision + "/" + urllib.parse.quote(path, safe="/")
            data = read_remote(url)
        actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if actual != oid:
            raise ValueError(f"HF raw file does not match locked Git blob: {path}")
        if not cached.exists():
            cached.write_bytes(data)
        p = subprocess.run(["git", "-C", str(destination), "hash-object", "-w", "--stdin"],
                           input=data, capture_output=True, timeout=30)
        if p.returncode or p.stdout.decode().strip() != oid:
            raise RuntimeError(f"Cannot store verified Git object for {path}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(hydrate, objects.items()))


def rate_limit_delay(headers):
    """Honor Retry-After (seconds/date), then HF's resolver reset; never retry tightly."""
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime
    import re
    value = headers.get("Retry-After")
    if value:
        try:
            return max(1.0, float(value))
        except ValueError:
            try:
                return max(1.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError):
                pass
    match = re.search(r"(?:^|;)\s*t=(\d+)", headers.get("RateLimit", ""))
    return float(match.group(1)) + 1 if match else 60.0


def checkout(spec, destination, fetch, task_paths=None):
    destination = Path(destination)
    revision = spec["revision"]
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Source revision must be an exact commit ID")
    if not destination.exists():
        if not fetch:
            raise FileNotFoundError(f"Source missing: {destination}; use --fetch or --source-root")
        destination.mkdir(parents=True)
        command(["git", "init", str(destination)])
        command(["git", "-C", str(destination), "remote", "add", "origin", spec["url"]])
    if fetch:
        remote = command(["git", "-C", str(destination), "remote", "get-url", "origin"])
        if remote != spec["url"]:
            raise ValueError(f"Source remote mismatch: {remote}")
        if source_is_dirty(destination):
            raise ValueError(f"Source is dirty before fetch: {destination}")
        # The dataset repositories also contain many unrelated benchmarks and large blobs.
        # Fetch the exact commit/tree, then materialize only requested task directories.
        if task_paths:
            for path in task_paths:
                contained(destination, path)
            command(["git", "-C", str(destination), "config", "remote.origin.promisor", "true"])
            command(["git", "-C", str(destination), "config", "remote.origin.partialclonefilter", "blob:none"])
            command(["git", "-C", str(destination), "sparse-checkout", "init", "--cone"],
                    env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"})
        command(["git", "-C", str(destination), "fetch", "--filter=blob:none", "--depth=1", "origin", revision], timeout=1800)
        if task_paths and spec["url"].startswith("https://huggingface.co/datasets/"):
            hydrate_hf_blobs(spec, destination, task_paths)
        if task_paths:
            command(["git", "-C", str(destination), "sparse-checkout", "add", *sorted(task_paths)],
                    env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}, timeout=1800)
        command(["git", "-C", str(destination), "checkout", "--detach", revision],
                env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"})
    actual = command(["git", "-C", str(destination), "rev-parse", "HEAD"])
    if actual != revision:
        raise ValueError(f"Revision mismatch: expected {revision}, got {actual}")
    if source_is_dirty(destination):
        raise ValueError(f"Source is dirty: {destination}")
    return destination


def validate_layout(path):
    for name in ("instruction.md", "task.toml", "environment/Dockerfile", "tests/test.sh"):
        if not (path / name).is_file():
            raise ValueError(f"Missing Harbor task file: {path / name}")
    cfg = tomllib.loads((path / "task.toml").read_text())
    for item in path.rglob("*"):
        if item.is_file():
            with item.open("rb") as f:
                if f.read(100).startswith(b"version https://git-lfs.github.com/spec/v1"):
                    raise ValueError(f"Unresolved Git LFS object: {item}")
    # Do not interpret regex matches in a test file as an output contract.
    tree_hash(path)
    return cfg


def apply_patch(task, patch_dir, upstream_hash):
    """Exact, preimage-hashed file edits; metadata is part of task provenance."""
    patch_dir = Path(patch_dir)
    meta = read_json(patch_dir / "metadata.json")
    if meta["upstream_task_hash"] != upstream_hash:
        raise ValueError(f"Patch {meta['id']} upstream hash mismatch")
    if meta["type"] not in {"environment", "reference", "verifier", "task_variant"}:
        raise ValueError("Unknown patch type")
    if meta["type"] in {"verifier", "task_variant"} and not meta.get("new_version"):
        raise ValueError("Scoring and task changes require a new version")
    if not meta.get("reason") or not meta.get("regression_evidence"):
        raise ValueError("Patch needs reason and regression evidence")
    from .io import evidence_ok
    if not all(evidence_ok(r, patch_dir) for r in meta["regression_evidence"]):
        raise ValueError("Patch regression evidence is missing or changed")
    edits = []
    for edit in meta["files"]:
        target = contained(task, edit["path"])
        replacement = contained(patch_dir, edit["replacement"])
        if file_hash(target) != edit["before_sha256"] or file_hash(replacement) != edit["after_sha256"]:
            raise ValueError(f"Patch context mismatch: {edit['path']}")
        edits.append((target, replacement))
    for target, replacement in edits:
        target.write_bytes(replacement.read_bytes())
    return {"id": meta["id"], "metadata_sha256": file_hash(patch_dir / "metadata.json"),
            "type": meta["type"], "new_version": meta.get("new_version")}


def download_asset(asset, cache):
    """External assets are opt-in and require an independently recorded SHA-256."""
    expected = asset.get("sha256")
    if not expected or len(expected) != 64:
        raise ValueError("External asset has no locked SHA-256; refusing first-use trust")
    target = Path(cache) / expected
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(asset["url"], timeout=120) as src, tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as out:
            shutil.copyfileobj(src, out)
            temp = Path(out.name)
        if file_hash(temp) != expected:
            temp.unlink()
            raise ValueError(f"Asset hash mismatch: {asset['url']}")
        temp.replace(target)
    if file_hash(target) != expected:
        raise ValueError(f"Cached asset corrupted: {target}")
    return target


def prepare_tasks(root, *, fetch=False, source_roots=None, selected=None):
    root = Path(root).resolve()
    lock = read_json(root / "sources.lock.yaml")
    rows = load_catalog(root)
    selected = set(selected or [r["task_key"] for r in rows])
    unknown = selected - {r["task_key"] for r in rows}
    if unknown:
        raise ValueError(f"Unknown tasks: {sorted(unknown)}")
    origins, failures, prepared = {}, [], []
    for source in sorted({r["source"] for r in rows if r["task_key"] in selected}):
        spec = lock["sources"][source]
        try:
            path = Path((source_roots or {}).get(source, root / ".cache/sources" / source / spec["revision"]))
            task_paths = [f"{spec['task_subdir']}/{r['upstream_task_id']}" for r in rows
                          if r["source"] == source and r["task_key"] in selected]
            origins[source] = checkout(spec, path, fetch, task_paths=task_paths)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            origins[source] = str(exc)
    for row in rows:
        if row["task_key"] not in selected:
            continue
        try:
            origin = origins[row["source"]]
            if isinstance(origin, str):
                raise RuntimeError(origin)
            spec = lock["sources"][row["source"]]
            original = origin / spec["task_subdir"] / row["upstream_task_id"]
            if fetch and original.exists():
                pointers = []
                for asset in original.rglob("*"):
                    if asset.is_file():
                        with asset.open("rb") as f:
                            if f.read(100).startswith(b"version https://git-lfs.github.com/spec/v1"):
                                pointers.append(asset)
                if pointers:
                    command(["git", "-C", str(origin), "lfs", "install", "--local", "--skip-smudge"])
                    if spec["url"].startswith("https://huggingface.co/datasets/"):
                        # git-lfs can traverse unrelated promisor objects even with --include.
                        # Use the SHA-256 and size in the already verified Git pointer itself.
                        for pointer in pointers:
                            fields = dict(line.split(" ", 1) for line in pointer.read_text().splitlines())
                            oid = fields.get("oid", "")
                            if not oid.startswith("sha256:") or not fields.get("size", "").isdigit():
                                raise ValueError(f"Malformed LFS pointer: {pointer}")
                            relative = pointer.relative_to(origin).as_posix()
                            url = (spec["url"].rstrip("/") + "/resolve/" + spec["revision"] + "/"
                                   + urllib.parse.quote(relative, safe="/"))
                            cached = download_asset({"url": url, "sha256": oid[7:]}, root / ".cache/assets")
                            if cached.stat().st_size != int(fields["size"]):
                                raise ValueError(f"LFS size mismatch: {relative}")
                            shutil.copyfile(cached, pointer)
                    else:
                        command(["git", "-C", str(origin), "lfs", "pull", "--include",
                                 original.relative_to(origin).as_posix() + "/**", "origin"], timeout=1800)
            cfg = validate_layout(original)
            raw_hash = tree_hash(original)
            snapshot = root / ".cache/raw" / raw_hash
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            if not snapshot.exists():
                shutil.copytree(original, snapshot, symlinks=True)
            if tree_hash(snapshot) != raw_hash:
                raise ValueError("Raw snapshot hash mismatch")
            destination = root / row["run_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=destination.parent) as work:
                staged = Path(work) / row["task_key"]
                shutil.copytree(snapshot, staged, symlinks=True)
                for pid in row["patch_ids"]:
                    meta = read_json(root / "patches" / pid / "metadata.json")
                    if meta["type"] in {"verifier", "task_variant"}:
                        raise ValueError("Verifier/task variants require a separate catalog/task version; default native task preparation refuses them")
                patches = [apply_patch(staged, root / "patches" / pid, raw_hash) for pid in row["patch_ids"]]
                adapted_hash = tree_hash(staged)
                if destination.exists():
                    if tree_hash(destination) != adapted_hash:
                        raise ValueError("Prepared task changed; refuse to overwrite. Move old version aside first.")
                else:
                    staged.rename(destination)
            inventory = tree_manifest(destination)
            env = cfg.get("environment", {})
            manifest = {"schema_version": 1, "task_key": row["task_key"], "source": spec,
                        "raw_task_hash": raw_hash, "adapted_task_hash": adapted_hash,
                        "files": inventory, "patches": patches,
                        "native_config": cfg, "backend": "docker",
                        "dockerfile_hash": file_hash(destination / "environment/Dockerfile"),
                        "image": {"declared": env.get("docker_image"), "resolved_digest": None, "status": "pending_build"},
                        "external_data_status": "pending_native_build_and_inventory",
                        "agent_visibility": "pending_image_inspection"}
            for asset in lock["external_assets"]:
                if row["task_key"] in asset.get("tasks", []):
                    download_asset(asset, root / ".cache/assets")
            write_json(root / "catalog/prepared" / f"{row['task_key']}.json", manifest)
            row.update(source_revision=spec["revision"], raw_task_hash=raw_hash,
                       adapted_task_hash=adapted_hash, source_difficulty=cfg.get("metadata", {}).get("difficulty"))
            prepared.append(row["task_key"])
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            failures.append({"task_key": row["task_key"], "reason": str(exc)})
    write_jsonl(root / "catalog/tasks.jsonl", rows)
    result = {"prepared": prepared, "failures": failures, "source_lock_hash": file_hash(root / "sources.lock.yaml")}
    write_json(root / "artifacts/prepare/result.json", result)
    return result


def check_harbor(config=None, task_paths=()):
    from harbor.models.job.config import JobConfig
    from harbor.models.task.task import Task
    if importlib.metadata.version("harbor") != "0.25.0":
        raise ValueError("Expected Harbor 0.25.0")
    if config:
        unknown = set(config) - JobConfig.model_fields.keys()
        if unknown:
            raise ValueError(f"Unknown Harbor configuration fields: {sorted(unknown)}")
    resolved = JobConfig.model_validate(config).model_dump(mode="json") if config else None
    for path in task_paths:
        Task(task_dir=Path(path))
    return resolved


def bind_qwen_bundle(root, config, manifest_path=None):
    """Bind verified installation bytes into native agent kwargs and experiment identity."""
    root = Path(root).resolve()
    lock = read_json(root / "sources.lock.yaml")["qwen_code"]
    pin = lock.get("installation_bundle")
    if not pin:
        raise ValueError("No Qwen installation bundle is locked for this runtime")
    manifest_path = Path(manifest_path) if manifest_path else root / pin["default_manifest"]
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest_path = manifest_path.resolve()
    manifest = read_json(manifest_path)
    expected = {"schema_version": 1, "kind": "dbt-bench-qwen-bundle-v1",
                "platform": pin["platform"], "qwen_version": lock["version"],
                "node_version": pin["node_version"], "npm_version": pin["npm_version"]}
    if any(manifest.get(k) != v for k, v in expected.items()):
        raise ValueError("Qwen bundle identity differs from source lock")
    if manifest.get("locks") != pin["locks"]:
        raise ValueError("Qwen bundle dependency locks differ")
    for item in pin["locks"]:
        if not evidence_ok(item, root):
            raise ValueError("Qwen dependency lock missing or changed")
    files = {}
    for name in ("archive", "inventory", "installer"):
        entry = manifest["files"][name]
        path = (manifest_path.parent / entry["path"]).resolve()
        if not path.is_relative_to(manifest_path.parent):
            raise ValueError("Qwen bundle file escapes manifest directory")
        if entry["sha256"] != pin["files"][name] or file_hash(path) != entry["sha256"]:
            raise ValueError("Qwen bundle file checksum mismatch: " + name)
        files[name] = {"path": str(path), "sha256": entry["sha256"]}
    for agent in config["agents"]:
        if agent.get("name") != "qwen-coder":
            raise ValueError("Fixed Qwen bundle requires the native qwen-coder adapter")
        kwargs = agent.setdefault("kwargs", {})
        if kwargs.get("version") != lock["version"]:
            raise ValueError("Qwen agent version differs from bundle")
        kwargs.update(installation_bundle=str(manifest_path),
                      installation_bundle_sha256=file_hash(manifest_path))
    return {"manifest": {"path": str(manifest_path), "sha256": file_hash(manifest_path)},
            "files": files, "platform": pin["platform"], "locks": pin["locks"]}


def emit_config(root, source, model, endpoint, deployment, *, output=None, tasks=None,
                attempts=1, concurrent=1, check=True, backend="docker", qwen_bundle=None):
    root = Path(root).resolve()
    rows = [r for r in load_catalog(root) if r["source"] == source]
    if tasks:
        wanted = set(tasks)
        rows = [r for r in rows if r["task_key"] in wanted]
        if {r["task_key"] for r in rows} != wanted:
            raise ValueError("Unknown task or source mismatch")
    if not rows or not model or not endpoint or not deployment or attempts < 1 or concurrent < 1:
        raise ValueError("Require tasks, model, endpoint, deployment ID and positive attempt/concurrency counts")
    paths = [resolve_task(root, row, backend) for row in rows]
    config = read_json(root / f"configs/{'spider' if source == 'spider2' else source}.yaml")
    configure_backend(config, backend)
    config["agents"][0].update(model_name=model, base_url=endpoint)
    config.update(n_attempts=attempts, n_concurrent_trials=concurrent)
    config["tasks"] = [{"path": str(path)} for path in paths]
    config["datasets"] = []
    config["jobs_dir"] = str(root / "jobs")
    installation = (bind_qwen_bundle(root, config, qwen_bundle)
                    if backend == "apptainer" or qwen_bundle is not None else
                    {"mode": "native_online_install", "dependency_freeze": "not_certified"})
    if check:
        config = check_harbor(config, paths)
    identity = {"backend": backend, "backend_bindings": {r["task_key"]: binding_identity(root, r, backend) for r in rows},
                "source_lock_hash": file_hash(root / "sources.lock.yaml"),
                "catalog_hash": file_hash(root / "catalog/tasks.jsonl"),
                "split_hash": read_json(root / "catalog/splits.json")["hash"],
                "config": config, "deployment": deployment, "qwen_installation": installation,
                "tasks": {r["task_key"]: r["adapted_task_hash"] for r in rows},
                "task_manifests": {r["task_key"]: file_hash(root / "catalog/prepared" / f"{r['task_key']}.json") for r in rows},
                "generation": {"client": "native Qwen defaults; actual values pending capture",
                               "server": "unknown", "thinking": "unknown", "context_limit": "unknown"},
                "validation": {r["task_key"]: file_hash(root / r["validation_ref"]) if (root / r["validation_ref"]).exists() else None for r in rows}}
    experiment_id = digest(identity)
    config["job_name"] = f"{source}-{experiment_id[:16]}"
    output = Path(output) if output else root / "artifacts/configs" / f"{config['job_name']}.yaml"
    if output.exists() and read_json(output) != config:
        raise ValueError("Refuse to overwrite a different run configuration")
    write_json(output, config)
    write_json(output.with_suffix(".manifest.json"), {"experiment_id": experiment_id, "identity": identity,
               "config_hash": file_hash(output), "runtime_schema_checked": check,
               "planned": [{"task_key": r["task_key"], "attempt": i} for r in rows for i in range(1, attempts + 1)]})
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("import-legacy")
    p.add_argument("--legacy", type=Path, required=True)
    p = sub.add_parser("tasks")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--source-root", action="append", default=[], metavar="SOURCE=CHECKOUT")
    p.add_argument("--task", action="append")
    p = sub.add_parser("sif", help="Register a complete native task SIF with hashed build provenance")
    p.add_argument("--task", required=True)
    p.add_argument("--image", type=Path, required=True)
    p.add_argument("--provenance", type=Path, required=True)
    p = sub.add_parser("config")
    p.add_argument("--source", choices=["ade", "spider2", "deb"], required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--endpoint", required=True)
    p.add_argument("--deployment", required=True)
    p.add_argument("--output", type=Path)
    p.add_argument("--task", action="append")
    p.add_argument("--attempts", type=int, default=1)
    p.add_argument("--concurrent", type=int, default=1)
    p.add_argument("--backend", choices=BACKENDS, default="docker")
    p.add_argument("--qwen-bundle", type=Path, help="Locked local installation manifest; Apptainer requires it or the pinned cache default")
    args = parser.parse_args(argv)
    if args.action == "import-legacy":
        print(json.dumps(import_legacy(args.root, args.legacy), indent=2))
    elif args.action == "tasks":
        result = prepare_tasks(args.root, fetch=args.fetch, source_roots=dict(x.split("=", 1) for x in args.source_root), selected=args.task)
        print(f"Prepared {len(result['prepared'])}; failed {len(result['failures'])}. See artifacts/prepare/result.json")
        return 1 if result["failures"] else 0
    elif args.action == "sif":
        row = next((r for r in load_catalog(args.root) if r["task_key"] == args.task), None)
        if row is None:
            parser.error("Unknown task")
        print(register_sif(args.root, row, args.image, args.provenance))
    else:
        print(emit_config(args.root, args.source, args.model, args.endpoint, args.deployment,
                          output=args.output, tasks=args.task, attempts=args.attempts, concurrent=args.concurrent,
                          backend=args.backend, qwen_bundle=args.qwen_bundle))
    return 0
