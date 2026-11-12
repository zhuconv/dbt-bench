from pathlib import Path
import shutil

from dbt_bench.io import digest, file_hash, read_json, tree_hash, write_json, write_jsonl, write_text

REPO = Path(__file__).resolve().parents[1]


def qwen_bundle_fixture(root):
    """Small integrity fixture, never executable or a benchmark installation."""
    root = Path(root)
    directory = root / '.cache/qwen-runtime/locked-v1'
    directory.mkdir(parents=True)
    files = {}
    for name in ('archive', 'inventory', 'installer'):
        path = directory / name
        path.write_text('synthetic identity fixture ' + name)
        files[name] = {'path': name, 'sha256': file_hash(path)}
    manifest = {'schema_version': 1, 'kind': 'dbt-bench-qwen-bundle-v1',
                'platform': 'linux-arm64', 'qwen_version': '0.26.5',
                'node_version': '22.23.4', 'npm_version': '10.9.9', 'locks': [], 'files': files}
    path = directory / 'bundle.json'
    write_json(path, manifest)
    lock = read_json(root / 'sources.lock.yaml')
    lock['qwen_code']['installation_bundle'] = {
        'default_manifest': str(path.relative_to(root)), 'platform': 'linux-arm64',
        'node_version': '22.23.4', 'npm_version': '10.9.9', 'locks': [],
        'files': {k: v['sha256'] for k, v in files.items()}}
    write_json(root / 'sources.lock.yaml', lock)
    return path


def fixture_root(root):
    root = Path(root)
    for f in ("sources.lock.yaml", "configs/validation.yaml", "validation/risk-register.json", "scripts/context_probe.py"):
        target = root / f
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / f, target)
    write_text(root / "src/dbt_bench/validation.py", "# fixture implementation identity\n")
    key = "ade__fixture"
    row = {"task_key": key, "source": "ade", "upstream_task_id": "fixture", "run_path": f"tasks/{key}",
           "raw_task_hash": None, "adapted_task_hash": None, "patch_ids": [], "split": "train",
           "split_version": "fixture-v1", "pool_status": "quarantined", "validation_ref": "validation/ade/fixture/record.json"}
    write_json(root / "catalog/import.json", {"candidate_ids_hash": digest([key])})
    write_jsonl(root / "catalog/tasks.jsonl", [row])
    split = {"assignments": {key: "train"}, "groups": {key: [key]}, "status": "provisional"}
    split["hash"] = digest(split)
    write_json(root / "catalog/splits.json", split)
    return row


def native_task(path):
    path = Path(path)
    files = {"instruction.md": "Create the requested fixture file.\n",
             "task.toml": 'version = "1.0"\n[agent]\ntimeout_sec = 60\n[verifier]\ntimeout_sec = 30\n',
             "environment/Dockerfile": "FROM busybox:1.36\nWORKDIR /app\n",
             "tests/test.sh": "#!/bin/sh\nmkdir -p /logs/verifier\nprintf 0 > /logs/verifier/reward.txt\n",
             "solution/solve.sh": "#!/bin/sh\nprintf fixture > /app/answer.txt\n"}
    for name, content in files.items():
        write_text(path / name, content)
    return path
