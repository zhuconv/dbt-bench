"""Import candidate identity and history, never historical validation conclusions."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import subprocess

from .io import digest, evidence_ok, file_hash, read_json, read_jsonl, unique_index, write_json, write_jsonl

EXPECTED_REVISION = "0f80b11a1a2dfc1b53c5106bf34a83f5a4a23098"
SOURCES = {"ade", "spider2", "deb"}
LISTS = ("train_tasks_v3", "test_tasks_v3", "train_tasks_v2", "test_tasks_v2")


def task_key(item):
    source, task = item["source"], item["task"]
    if source not in SOURCES or not re.fullmatch(r"[A-Za-z0-9_-]+", task):
        raise ValueError(f"Invalid candidate: {item}")
    return f"{source}__{task}"


def import_legacy(root, legacy):
    root, legacy = Path(root), Path(legacy)
    lists = {n: read_json(legacy / "code/data" / f"{n}.json") for n in LISTS}
    for rows in lists.values():
        unique_index([{"key": task_key(r)} for r in rows], "key")
    train = {task_key(x) for x in lists["train_tasks_v3"]}
    test = {task_key(x) for x in lists["test_tasks_v3"]}
    if train & test:
        raise ValueError(f"Conflicting v3 splits: {sorted(train & test)}")
    union = {task_key(x): x for n in LISTS[:2] for x in lists[n]}
    revision = subprocess.run(["git", "-C", str(legacy), "rev-parse", "HEAD"],
                              text=True, capture_output=True, check=True).stdout.strip()
    status = subprocess.run(["git", "-C", str(legacy), "status", "--porcelain"],
                            text=True, capture_output=True, check=True).stdout.strip()
    files = {f"code/data/{n}.json": file_hash(legacy / "code/data" / f"{n}.json") for n in LISTS}
    excluded = read_json(legacy / "code/harbor-cmp/excluded_tasks.json")
    files["code/harbor-cmp/excluded_tasks.json"] = file_hash(legacy / "code/harbor-cmp/excluded_tasks.json")
    counts = dict(Counter(x["source"] for x in union.values()))
    prior = root / "catalog/import.json"
    identity = {"revision": revision, "files": files}
    if prior.exists() and read_json(prior)["input_identity"] != identity:
        raise ValueError("Legacy input changed; create a new catalog version, do not overwrite v1")
    old = unique_index(read_jsonl(root / "catalog/tasks.jsonl"), "task_key") if (root / "catalog/tasks.jsonl").exists() else {}
    records = []
    assignments = {}
    groups = {}
    for key, item in sorted(union.items()):
        history = [{"version": n.rsplit("_", 1)[1], "split": n.split("_")[0],
                    "evidence": f"catalog/legacy/{n}.json", "training_use": "unknown"}
                   for n in LISTS if key in {task_key(x) for x in lists[n]}]
        split = "train" if key in train else "test"
        # Only the explicitly identified prompt variants are grouped without new assets.
        group = key.removesuffix("-hard") if key.startswith("ade__ade-bench-f1003") else key
        if key.startswith("ade__ade-bench-f1010"):
            group = "ade__ade-bench-f1010"
        groups.setdefault(group, []).append(key)
        assignments[key] = split
        record = {
            "schema_version": 1, "task_key": key, "source": item["source"],
            "upstream_task_id": item["task"], "source_revision": None,
            "raw_task_hash": None, "adapted_task_hash": None, "run_path": f"tasks/{key}",
            "data": {"backend": "duckdb", "assets": [], "status": "pending"},
            "backend": "docker", "patch_ids": [], "workflow": "unknown",
            "project_family": "unknown", "warehouse_family": "unknown",
            "task_family": group, "family_review": "pending",
            "split": split, "split_version": "v1-legacy-provisional",
            "legacy_split": {h["version"]: h["split"] for h in history},
            "exposure_history": history, "legacy_reason": item.get("reason"),
            "legacy_exclusion": excluded.get(key), "difficulty": "unmeasured",
            "source_difficulty": None, "selection_reason": "Member of the supplied v3 candidate union",
            "pool_status": "quarantined", "quarantine_reason": "New validation not yet complete",
            "validation_ref": f"validation/{item['source']}/{item['task']}/record.json",
        }
        # Preparation and reviewed metadata survive an idempotent re-import.
        records.append(old.get(key, record))
    for group, members in groups.items():
        if len({assignments[k] for k in members}) != 1:
            raise ValueError(f"Prompt variant group crosses splits: {group}")
    for name, rows in lists.items():
        write_json(root / f"catalog/legacy/{name}.json", rows)
    write_json(root / "catalog/legacy/excluded_tasks.json", excluded)
    moved = sorted(test & {task_key(x) for x in lists["train_tasks_v2"]})
    manifest = {
        "schema_version": 1, "input_identity": identity, "legacy_worktree_status": status,
        "expected_revision": EXPECTED_REVISION, "revision_matches_plan": revision == EXPECTED_REVISION,
        "counts": counts, "candidate_count": len(records), "candidate_ids_hash": digest(sorted(union)),
        "expected_counts": {"ade": 16, "spider2": 55, "deb": 64},
        "counts_match_plan": counts == {"ade": 16, "spider2": 55, "deb": 64},
        "v2_train_moved_to_v3_test": moved, "archive_sha256_verified": False,
        "legacy_validation_imported": False,
    }
    write_json(root / "catalog/import.json", manifest)
    write_jsonl(root / "catalog/tasks.jsonl", records)
    splits = {"schema_version": 1, "version": "v1-legacy-provisional", "status": "provisional",
              "assignments": assignments, "groups": groups,
              "rationale": "Preserve v3 history; known ADE prompt variants grouped. Cross-source setup/project and DEB family review pending. Not a certified clean holdout.",
              "candidate_ids_hash": digest(sorted(union))}
    splits["hash"] = digest(splits)
    if not (root / "catalog/splits.json").exists():
        write_json(root / "catalog/splits.json", splits)
    return manifest


def load_catalog(root):
    rows = read_jsonl(Path(root) / "catalog/tasks.jsonl")
    index = unique_index(rows, "task_key")
    expected = read_json(Path(root) / "catalog/import.json")
    if digest(sorted(index)) != expected["candidate_ids_hash"]:
        raise ValueError("Catalog candidate IDs differ from imported union")
    splits = read_json(Path(root) / "catalog/splits.json")
    if digest({k: v for k, v in splits.items() if k != "hash"}) != splits["hash"]:
        raise ValueError("Split hash mismatch")
    if set(splits["assignments"]) != set(index):
        raise ValueError("Split coverage differs from catalog")
    members = [k for group in splits["groups"].values() for k in group]
    if sorted(members) != sorted(index):
        raise ValueError("Split groups must partition candidates exactly once")
    for group in splits["groups"].values():
        if len({splits["assignments"][k] for k in group}) != 1:
            raise ValueError("Family crosses splits")
    for row in rows:
        if row["split"] != splits["assignments"][row["task_key"]]:
            raise ValueError("Catalog and split disagree")
    if splits.get("status") == "reviewed_frozen":
        validate_frozen_split(root, index, splits)
    return rows


def validate_frozen_split(root, index, splits):
    """A frozen label alone is not a family/split review certificate."""
    ref = splits.get("review")
    if not evidence_ok(ref, root):
        raise ValueError("Frozen split review missing or changed")
    review = read_json(Path(root) / ref["path"])
    if (review.get("kind") != "reviewed-family-split-v1"
            or not splits.get("version") or review.get("version") != splits["version"]
            or review.get("candidate_ids_hash") != digest(sorted(index))
            or review.get("assignments") != splits["assignments"]
            or review.get("groups") != splits["groups"]
            or set(review.get("tasks", {})) != set(index)):
        raise ValueError("Frozen split differs from reviewed decisions")
    inputs = review.get("input_reviews", [])
    if not inputs or not all(evidence_ok(item, root) for item in inputs):
        raise ValueError("Frozen family review inputs missing or changed")
    groups = {key: group for group, members in splits["groups"].items() for key in members}
    required = {"split", "split_version", "task_family", "workflow", "project_family",
                "warehouse_family", "legacy_split", "exposure_history"}
    for key, row in index.items():
        reviewed = review["tasks"][key]
        if (not required <= reviewed.keys() or any(row.get(k) != v for k, v in reviewed.items())
                or row.get("split_version") != splits["version"]
                or row.get("task_family") != groups[key]
                or row.get("split") not in {"train", "test"}
                or not isinstance(row.get("family_review"), dict)
                or row["family_review"].get("evidence") != ref):
            raise ValueError(f"Catalog family metadata differs from frozen review: {key}")
