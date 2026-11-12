"""Read-only Harbor reconciliation and conservative, versioned training eligibility."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path, PurePosixPath

from .catalog import load_catalog
from .io import (contained, digest, evidence_ok, evidence_ref, file_hash, read_json, read_jsonl,
                 tree_hash, tree_manifest, unique_index, write_json, write_jsonl)
from .trace import inspect_trial_trace
from .validation import identity, reusable, task_dir
from .submission import inspect_submission as inspect_legacy_submission

POLICY_VERSION = "training-candidates-v5"


def inspect_submission(trial_dir, spec):
    """Check native file/directory captures without changing legacy receipts.

    Schema 2 adds individually declared files, allowing a project/config/output
    capture without exporting the whole working directory. Types are declared,
    never guessed from a filename extension. This is byte completeness only.
    """
    if spec.get('schema_version', 1) == 1:
        return inspect_legacy_submission(trial_dir, spec)
    directory = Path(trial_dir) / 'artifacts'
    result = {'complete': False, 'errors': [], 'files': [], 'snapshot_hash': None,
              'scope': 'Collected bytes only; semantic correctness and checker binding are separate'}
    try:
        if spec.get('schema_version') != 2:
            raise ValueError('Unsupported submission capture schema')
        entries = read_json(directory / 'manifest.json')
        declarations, kinds = spec['artifacts'], spec['artifact_types']
        if (not isinstance(entries, list) or not declarations
                or set(kinds) != {item['source'] for item in declarations}):
            raise ValueError('Invalid artifact declarations or native manifest')
        sources, destinations, files = [], [], []
        for declared in declarations:
            source, destination = declared['source'], declared['destination']
            for value, absolute, seen in ((source, True, sources), (destination, False, destinations)):
                path = PurePosixPath(value)
                if (str(path) != value or path.is_absolute() != absolute or '..' in path.parts
                        or '\\' in value or value in ('.', '/', 'manifest.json')
                        or any(path == old or path in old.parents or old in path.parents for old in seen)):
                    raise ValueError('Invalid or overlapping artifact paths')
                seen.append(path)
            kind = kinds[source]
            if kind not in ('file', 'directory') or declared.get('service') not in (None, 'main'):
                raise ValueError('Unsupported artifact type/service')
            if kind == 'file' and declared.get('exclude', []):
                raise ValueError('File artifact has directory exclusions')
            matching = [e for e in entries if e.get('source') == source
                        and e.get('service') in (None, 'main')]
            if len(matching) != 1:
                raise ValueError('Missing or duplicated artifact source: ' + source)
            entry = matching[0]
            if (entry.get('status') != 'ok' or entry.get('type') != kind
                    or entry.get('destination') != 'artifacts/' + destination
                    or entry.get('exclude') != declared.get('exclude', [])):
                raise ValueError('Artifact collection failed or differs from declaration: ' + source)
            target = contained(directory, destination)
            # Require a real exported artifact, not a link into another capture.
            if (directory / destination).is_symlink():
                raise ValueError('Artifact root is a symlink')
            if kind == 'file':
                if not target.is_file():
                    raise ValueError('Declared artifact file is missing')
                files.append({'path': destination, 'sha256': file_hash(target),
                              'executable': bool(target.stat().st_mode & 0o111)})
            else:
                inventory = tree_manifest(target)
                if not inventory:
                    raise ValueError('Empty submission directory')
                files.extend({**f, 'path': destination + '/' + f['path']} for f in inventory)
        required = spec['required_files']
        captured = {f['path'] for f in files}
        if not required or len(set(required)) != len(required):
            raise ValueError('Require distinct captured files')
        for relative in required:
            if relative not in captured or not contained(directory, relative).is_file():
                raise ValueError('Required captured file missing: ' + relative)
        files.sort(key=lambda f: f['path'])
        result.update(complete=True, files=files, snapshot_hash=digest(files),
                      native_manifest=evidence_ref(directory / 'manifest.json', Path(trial_dir)))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result['errors'].append(str(exc))
    return result


def split_provenance(root, row, experiment_identity):
    """Keep the generation identity separate from the current selection policy."""
    splits = read_json(Path(root) / "catalog/splits.json")
    return {
        "generation": {"catalog_hash": experiment_identity.get("catalog_hash"),
                       "split_hash": experiment_identity.get("split_hash")},
        "selection": {"catalog_hash": file_hash(Path(root) / "catalog/tasks.jsonl"),
                      "split_hash": splits["hash"], "split_version": splits.get("version"),
                      "split": row.get("split") if row else "unknown",
                      "review": splits.get("review")},
        "historical_exposure": row.get("exposure_history", []) if row else [],
        "scope": "Current selection membership; original generation identity and scores are unchanged. Historical train exposure is retained and does not become clean holdout evidence.",
    }


def native_success(rewards, rule):
    if not isinstance(rewards, dict) or rule.get("verification") != "reviewed":
        return None
    value = rewards.get(rule["metric"])
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value == rule["equals"]


def reviewed_success_rule(root, row, validation, source_rule, *, current):
    """A reviewed task definition does not certify every task in its source."""
    if current and validation and validation.get('review_current'):
        path = task_dir(root, row)/'contract.yaml'
        if path.is_file() and file_hash(path) == validation.get('identity', {}).get('contract'):
            rule = read_json(path).get('native_success') or {}
            if (rule.get('reviewed') is True and isinstance(rule.get('metric'), str)
                    and rule['metric'] and type(rule.get('equals')) in (int, float)):
                return {'rule':{'verification':'reviewed', 'metric':rule['metric'], 'equals':rule['equals']},
                        'scope':'task_contract', 'evidence':evidence_ref(path,root)}
    return {'rule':source_rule, 'scope':'source', 'evidence':None}


def eligibility(trial, validation, *, split_status, root):
    reasons = []
    if trial.get("split") != "train":
        reasons.append("not_train_split")
    if split_status != "reviewed_frozen":
        reasons.append("split_not_reviewed_frozen")
    if not trial.get("identity_verified"):
        reasons.append("run_identity_unverified")
    if trial.get("environment_valid") is not True:
        reasons.append("environment_unverified_or_invalid")
    if trial.get("native_reward_valid") is not True or trial.get("native_success") is not True:
        reasons.append("native_success_not_trusted")
    if not trial.get("validation_current"):
        reasons.append("validation_stale_or_missing")
    if trial.get('submission_verified') is not True:
        reasons.append('submission_missing_or_unverified')
    native = (validation or {}).get("native", {}).get("conclusion")
    if native == "weak":
        if (validation or {}).get("audit", {}).get("conclusion") != "validated" or trial.get("audit_success") is not True:
            reasons.append("weak_native_requires_validated_audit")
        if not trial.get("same_submission_verified"):
            reasons.append("audit_submission_not_bound")
    elif native != "validated":
        reasons.append("task_not_validated")
    quality = trial.get("trace_quality", {})
    if quality.get("schema_valid") is not True or quality.get("content_fidelity") != "complete":
        reasons.append("trace_not_complete")
    candidate = not reasons
    return {"policy_version": POLICY_VERSION, "candidate": candidate,
            "strict_sft_ready": candidate and quality.get("model_context_capture") == "complete",
            "reasons": reasons}


def reconcile(planned, trials):
    pairs = [(p["task_key"], p["attempt"]) for p in planned]
    if len(pairs) != len(set(pairs)):
        raise ValueError("Duplicate planned task/attempt")
    ids = unique_index(trials, "trial_id")
    actual, errors = set(), []
    primary = Counter()
    for trial in trials:
        pair = (trial.get("task_key"), trial.get("attempt"))
        if pair not in set(pairs):
            errors.append({"kind": "unknown_task_attempt", "trial_id": trial["trial_id"], "pair": pair})
        else:
            actual.add(pair)
        parent = trial.get("retry_of")
        if parent:
            if parent not in ids or (ids[parent].get("task_key"), ids[parent].get("attempt")) != pair:
                errors.append({"kind": "invalid_retry_parent", "trial_id": trial["trial_id"]})
            seen, current = {trial["trial_id"]}, parent
            while current in ids:
                if current in seen:
                    errors.append({"kind": "retry_cycle", "trial_id": trial["trial_id"]})
                    break
                seen.add(current)
                current = ids[current].get("retry_of")
        else:
            primary[pair] += 1
    for pair, count in primary.items():
        if count > 1:
            errors.append({"kind": "duplicate_attempt", "pair": pair, "count": count})
    missing = [{"task_key": k, "attempt": a} for k, a in sorted(set(pairs) - actual)]
    return {"planned_attempts": len(pairs), "observed_trials": len(trials),
            "observed_attempts": len(actual), "infrastructure_retries": sum(bool(t.get("retry_of")) for t in trials),
            "missing": missing, "errors": errors}


def _reward_validity(data):
    # Preserve a serialized native field, or compute it with the pinned Harbor model.
    if "reward_valid" in data:
        return data["reward_valid"], "native_serialized"
    try:
        import importlib.metadata
        from harbor.models.trial.result import TrialResult
        if importlib.metadata.version("harbor") == "0.25.0":
            return TrialResult.model_validate(data).reward_valid, "native_model_0.25.0"
    except (ImportError, ValueError):
        pass
    return None, "unknown_harbor_model_unavailable_or_invalid_result"


def verify_submission(root, trial_dir, native_config, binding, experiment_identity, bound):
    submission_verified = False
    submission_capture = None
    capture_spec = experiment_identity.get('submission_capture', {}).get('spec')
    if evidence_ok(capture_spec, root) and evidence_ok(binding.get('submission'), root):
        spec = read_json(Path(root) / capture_spec['path'])
        saved = read_json(Path(root) / binding['submission']['path'])
        submission_capture = inspect_submission(trial_dir, spec)
        configured = native_config.get('artifacts', [])
        declarations_match = all(any(
            isinstance(entry, dict) and entry.get('source') == item['source']
            and entry.get('destination') == item['destination']
            and entry.get('exclude', []) == item.get('exclude', [])
            and entry.get('service') in (None, 'main') for entry in configured)
            for item in spec['artifacts'])
        submission_verified = bool(bound and declarations_match and spec.get('task_key') == binding.get('task_key')
            and submission_capture['complete'] and saved.get('complete') is True
            and saved.get('snapshot_hash') == submission_capture['snapshot_hash']
            and saved.get('files') == submission_capture['files'])
    return submission_verified, submission_capture


def collect_trial(root, result_path, binding, experiment, catalog, validations, split_status, lock):
    data = read_json(result_path)
    tid = data["id"]
    task = binding.get("task_key")
    row = catalog.get(task)
    environment = binding.get("environment", {})
    validation = validations.get(task)
    reward_valid, validity_source = _reward_validity(data)
    rewards = (data.get("verifier_result") or {}).get("rewards")
    current = False
    if row and validation:
        backend = validation.get("identity", {}).get("backend", "docker")
        current = (backend == experiment["identity"].get("backend", "docker") and
                   reusable(validation, identity(root, row, task_dir(root, row) / "contract.yaml", backend), root))
    artifact_refs = [evidence_ref(p, root) for p in sorted(result_path.parent.rglob("*")) if p.is_file()]
    bound = bool(row and binding.get("result_sha256") == file_hash(result_path)
                 and binding.get("experiment_id") == experiment["experiment_id"]
                 and binding.get("adapted_task_hash") == experiment["identity"]["tasks"].get(task)
                 and row["adapted_task_hash"] == binding.get("adapted_task_hash")
                 and binding.get("resolved_config_evidence")
                 and evidence_ok(binding["resolved_config_evidence"], root))
    if bound:
        observed = read_json(Path(root) / binding["resolved_config_evidence"]["path"])
        bound = observed.get("experiment_id") == experiment["experiment_id"] and observed.get("task_hash") == row["adapted_task_hash"] and observed.get("trial_id") == tid
    quality = inspect_trial_trace(result_path.parent, root=root, context=binding.get("context_capture"),
                                  conversion=binding.get('trace_conversion'))
    submission_verified, submission_capture = verify_submission(
        root, result_path.parent, data.get("config", {}), binding, experiment["identity"], bound)
    audit = binding.get("audit")
    audit_reward, audit_success, same_snapshot = None, None, False
    if audit and evidence_ok(audit.get("result"), root) and evidence_ok(binding.get("submission"), root):
        audit_result = read_json(Path(root) / audit["result"]["path"])
        audit_reward = audit_result.get("reward")
        same_snapshot = submission_verified and audit_result.get("submission_sha256") == binding["submission"]["sha256"]
        expected_checker = (validation or {}).get("audit", {}).get("checker_version")
        if same_snapshot and expected_checker and audit_result.get("checker_version") == expected_checker:
            audit_success = audit_result.get("success")
    environment_valid = environment.get("valid") if evidence_ok(environment.get("evidence"), root) else None
    success_definition = (reviewed_success_rule(root,row,validation,lock['sources'][row['source']]['success'],current=current)
                          if row else None)
    annotation = binding.get("cause_annotation")
    if annotation and (annotation.get("category") not in {1, 2, 3, 4} or not annotation.get("reason") or not evidence_ok(annotation.get("evidence"), root)):
        annotation = {"status": "pending_review", "submitted": annotation}
    trial = {"schema_version": 1, "trial_id": tid, "trial_name": data.get("trial_name"),
             "job_id": data.get("config", {}).get("job_id"), "task_key": task,
             "native_task_name": data.get("task_name"), "experiment_id": experiment["experiment_id"],
             "attempt": binding.get("attempt"), "retry_of": binding.get("retry_of"),
             "retry_index": binding.get("retry_index"), "source": row["source"] if row else "unknown",
             "split": row["split"] if row else "unknown", "split_version": row["split_version"] if row else "unknown",
             "split_provenance": split_provenance(root, row, experiment["identity"]),
             "native_rewards": rewards, "native_reward_valid": reward_valid, "reward_validity_source": validity_source,
             "native_success": native_success(rewards,success_definition['rule']) if success_definition else None,
             "native_success_definition": success_definition,
             "native_verifier_result": data.get("verifier_result"), "native_exception_info": data.get("exception_info"),
             "native_termination": data.get("termination_reason"), "native_failure_cause": data.get("failure_cause"),
             "native_step_results": data.get("step_results"), "agent_info": data.get("agent_info"),
             "native_agent_result": data.get("agent_result"), "started_at": data.get("started_at"), "finished_at": data.get("finished_at"),
             "native_timing": {k: data.get(k) for k in ("environment_setup", "agent_setup", "agent_execution", "verifier")},
             "identity_verified": bound, "environment_valid": environment_valid,
             "validation_current": current, "validation_ref": row["validation_ref"] if row else None,
             "submission_verified": submission_verified, "submission_capture": submission_capture,
             "audit_reward": audit_reward, "audit_success": audit_success,
             "same_submission_verified": same_snapshot, "trace_quality": quality,
             "cause_annotation": annotation, "artifacts": artifact_refs}
    trial["eligibility"] = eligibility(trial, validation, split_status=split_status, root=root)
    return trial


def report(root, *, jobs=(), experiment_path=None, bindings_path=None, output=None):
    root = Path(root).resolve()
    output = Path(output) if output else root / "reports/latest"
    catalog = unique_index(load_catalog(root), "task_key")
    records = read_jsonl(root / "validation/ledger.jsonl")
    validations = unique_index(records, "task_key")
    if set(validations) != set(catalog):
        raise ValueError("Validation ledger coverage differs from catalog")
    splits = read_json(root / "catalog/splits.json")
    lock = read_json(root / "sources.lock.yaml")
    if jobs and not experiment_path:
        raise ValueError("Job reporting requires the pre-run experiment manifest")
    bindings = unique_index(read_jsonl(bindings_path), "trial_id") if bindings_path else {}
    trials = []
    experiment = read_json(experiment_path) if experiment_path else None
    if experiment:
        if digest(experiment["identity"]) != experiment["experiment_id"]:
            raise ValueError("Experiment identity hash mismatch")
        if set(p["task_key"] for p in experiment["planned"]) - catalog.keys():
            raise ValueError("Experiment contains unknown candidate IDs")
    seen_paths = set()
    for job in jobs:
        for p in sorted(Path(job).rglob("result.json")):
            if p.resolve() in seen_paths:
                continue
            seen_paths.add(p.resolve())
            data = read_json(p)
            if "trial_name" not in data:
                continue
            trials.append(collect_trial(root, p, bindings.get(data["id"], {}), experiment,
                                        catalog, validations, splits["status"], lock))
    coverage = reconcile(experiment["planned"], trials) if experiment else {
        "planned_attempts": 0, "observed_trials": 0, "observed_attempts": 0,
        "infrastructure_retries": 0, "missing": [], "errors": []}
    unused = set(bindings) - {t["trial_id"] for t in trials}
    coverage["errors"].extend({"kind": "binding_without_result", "trial_id": tid} for tid in sorted(unused))
    groups = defaultdict(list)
    planned_groups = defaultdict(list)
    for t in trials:
        groups[(t["source"], t["split"], t["experiment_id"])].append(t)
    if experiment:
        for planned in experiment["planned"]:
            row = catalog[planned["task_key"]]
            planned_groups[(row["source"], row["split"], experiment["experiment_id"])].append(planned)
    stats = []
    for source, split, eid in sorted(set(groups) | set(planned_groups)):
        group = groups[(source, split, eid)]
        planned_group = planned_groups[(source, split, eid)]
        observed_pairs = {(t["task_key"], t["attempt"]) for t in group}
        missing = [p for p in planned_group if (p["task_key"], p["attempt"]) not in observed_pairs]
        stats.append({"source": source, "split": split, "experiment_id": eid,
                      "planned_attempts": len(planned_group), "observed_attempts": len(observed_pairs),
                      "missing_attempts": missing,
                      "trials": len(group), "valid_native_rewards": sum(t["native_reward_valid"] is True for t in group),
                      "invalid_native_rewards": sum(t["native_reward_valid"] is False for t in group),
                      "unknown_native_validity": sum(t["native_reward_valid"] is None for t in group),
                      "missing_reward": sum(not t["native_rewards"] for t in group),
                      "native_successes": sum(t["native_success"] is True for t in group),
                      "valid_success_denominator": sum(t["native_reward_valid"] is True and t["native_success"] is not None for t in group),
                      "valid_native_successes": sum(t["native_reward_valid"] is True and t["native_success"] is True for t in group),
                      "unknown_success": sum(t["native_success"] is None for t in group),
                      "audit_scored": sum(t["audit_reward"] is not None for t in group),
                      "audit_successes": sum(t["audit_success"] is True for t in group),
                      "retries": sum(bool(t["retry_of"]) for t in group),
                      "trace_content": dict(Counter(t["trace_quality"]["content_fidelity"] for t in group)),
                      "cause_categories": dict(Counter(str((t.get("cause_annotation") or {}).get("category", "pending_review")) for t in group)),
                      "token_metrics": {metric: {"total_reported": sum((t.get("native_agent_result") or {}).get(metric) or 0 for t in group),
                                                  "missing": sum((t.get("native_agent_result") or {}).get(metric) is None for t in group)}
                                        for metric in ("n_input_tokens", "n_output_tokens", "n_cache_tokens")}})
    stale = []
    for key, record in validations.items():
        row = catalog[key]
        if not reusable(record, identity(root, row, task_dir(root, row) / "contract.yaml", record.get("identity", {}).get("backend", "docker")), root):
            stale.append(key)
    summary = {"policy_version": POLICY_VERSION, "catalog_count": len(catalog),
               "selection_identity": {"catalog_hash": file_hash(root / "catalog/tasks.jsonl"),
                                      "split_hash": splits["hash"], "split_version": splits.get("version"),
                                      "split_status": splits["status"], "review": splits.get("review")},
               "generation_identity": {"experiment_id": experiment["experiment_id"],
                                       "catalog_hash": experiment["identity"].get("catalog_hash"),
                                       "split_hash": experiment["identity"].get("split_hash")} if experiment else None,
               "validation": dict(Counter(r["native"]["conclusion"] for r in records)),
               "stale_validation": stale, "coverage": coverage, "groups": stats,
               "quarantined_tasks": sum(r["pool_status"] == "quarantined" for r in catalog.values()),
               "candidate_count": sum(t["eligibility"]["candidate"] for t in trials),
               "strict_sft_ready_count": sum(t["eligibility"]["strict_sft_ready"] for t in trials)}
    write_jsonl(output / "trials.jsonl", trials)
    write_jsonl(output / "training-candidates.jsonl", [t for t in trials if t["eligibility"]["candidate"]])
    write_json(output / "summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--job", type=Path, action="append", default=[])
    parser.add_argument("--experiment", type=Path)
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = report(args.root, jobs=args.job, experiment_path=args.experiment,
                    bindings_path=args.bindings, output=args.output)
    print(json.dumps(result, indent=2))
    return 1 if result["coverage"]["errors"] or result["coverage"]["missing"] or result["stale_validation"] else 0
